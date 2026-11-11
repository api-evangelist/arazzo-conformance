// Command libopenapi-arazzo puts a CLI on github.com/pb33f/libopenapi/arazzo, which ships as a library only.
//
//	libopenapi-arazzo validate <file>                      exit 1 + one line per error when invalid
//	libopenapi-arazzo run <file> <workflowId> <inputs-json>  exit 1 when the workflow fails
//
// The library leaves HTTP to the caller (arazzo.Executor), so `run` carries a minimal executor. It is the harness's,
// not libopenapi's: it places each parameter where the source OpenAPI operation declares it (ExecutionRequest carries
// parameters by name only), sends JSON bodies, and decodes JSON responses.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/pb33f/libopenapi"
	"github.com/pb33f/libopenapi/arazzo"
	v3 "github.com/pb33f/libopenapi/datamodel/high/v3"
)

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintln(os.Stderr, "usage: libopenapi-arazzo validate <file> | run <file> <workflowId> <inputs-json>")
		os.Exit(2)
	}
	raw, err := os.ReadFile(os.Args[2])
	if err != nil {
		fail(2, err)
	}
	doc, err := libopenapi.NewArazzoDocument(raw)
	if err != nil {
		fail(1, fmt.Errorf("parse: %w", err))
	}
	switch os.Args[1] {
	case "validate":
		if res := arazzo.Validate(doc); res != nil && res.HasErrors() {
			fmt.Println(res.Error())
			os.Exit(1)
		}
		fmt.Println("valid")
	case "run":
		if len(os.Args) < 4 {
			fail(2, fmt.Errorf("run needs a workflowId"))
		}
		inputs := map[string]any{}
		if len(os.Args) > 4 && os.Args[4] != "" {
			if err := json.Unmarshal([]byte(os.Args[4]), &inputs); err != nil {
				fail(2, fmt.Errorf("inputs: %w", err))
			}
		}
		abs, _ := filepath.Abs(os.Args[2])
		sources, err := arazzo.ResolveSources(doc, &arazzo.ResolveConfig{
			BaseURL: "file://" + filepath.Dir(abs) + "/", FSRoots: []string{"/"},
			OpenAPIFactory: func(u string, b []byte) (*v3.Document, error) {
				d, err := libopenapi.NewDocument(b)
				if err != nil {
					return nil, err
				}
				m, err := d.BuildV3Model()
				if err != nil {
					return nil, err
				}
				return &m.Model, nil
			},
		})
		if err != nil {
			fail(1, fmt.Errorf("sources: %w", err))
		}
		res, err := arazzo.NewEngine(doc, executor{}, sources).RunWorkflow(context.Background(), os.Args[3], inputs)
		if err != nil {
			fail(1, err)
		}
		if res == nil || !res.Success {
			if res != nil {
				for _, st := range res.Steps {
					if st.Error != nil {
						fmt.Printf("step %s: %v\n", st.StepId, st.Error)
					}
				}
				if res.Error != nil {
					fmt.Println("workflow:", res.Error)
				}
			}
			fmt.Println("status=failed")
			os.Exit(1)
		}
		fmt.Println("status=success")
	default:
		fail(2, fmt.Errorf("unknown command %q", os.Args[1]))
	}
}

func fail(code int, err error) {
	fmt.Println("error:", err)
	os.Exit(code)
}

type executor struct{}

func (executor) Execute(ctx context.Context, req *arazzo.ExecutionRequest) (*arazzo.ExecutionResponse, error) {
	if req.Source == nil || req.Source.OpenAPIDocument == nil {
		return nil, fmt.Errorf("no OpenAPI source for %q", req.OperationID)
	}
	d := req.Source.OpenAPIDocument
	var method, path string
	var op *v3.Operation
	for p := d.Paths.PathItems.First(); p != nil && op == nil; p = p.Next() {
		for o := p.Value().GetOperations().First(); o != nil; o = o.Next() {
			if o.Value().OperationId == req.OperationID {
				method, path, op = strings.ToUpper(o.Key()), p.Key(), o.Value()
				break
			}
		}
	}
	if op == nil {
		return nil, fmt.Errorf("operation %q not found", req.OperationID)
	}
	in := map[string]string{}
	for _, p := range op.Parameters {
		in[p.Name] = p.In
	}
	q, h := url.Values{}, http.Header{}
	for name, v := range req.Parameters {
		s := fmt.Sprint(v)
		switch in[name] {
		case "path":
			path = strings.ReplaceAll(path, "{"+name+"}", url.PathEscape(s))
		case "query":
			q.Set(name, s)
		case "header":
			h.Set(name, s)
		}
	}
	u := strings.TrimRight(d.Servers[0].URL, "/") + path
	if len(q) > 0 {
		u += "?" + q.Encode()
	}
	var body io.Reader
	if req.RequestBody != nil {
		b, err := json.Marshal(req.RequestBody)
		if err != nil {
			return nil, err
		}
		body = bytes.NewReader(b)
		ct := req.ContentType
		if ct == "" {
			ct = "application/json"
		}
		h.Set("Content-Type", ct)
	}
	hr, err := http.NewRequestWithContext(ctx, method, u, body)
	if err != nil {
		return nil, err
	}
	hr.Header = h
	resp, err := http.DefaultClient.Do(hr)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	var out any
	if json.Unmarshal(b, &out) != nil {
		out = string(b)
	}
	return &arazzo.ExecutionResponse{StatusCode: resp.StatusCode, Headers: resp.Header, Body: out, URL: u, Method: method}, nil
}
