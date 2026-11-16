#!/usr/bin/env python3
"""Check every step's parameters against the operation it calls -- what no Arazzo validator in the suite checks.

A step whose operation was relinked or regenerated can keep passing schema and semantic validation while sending
path parameters the operation no longer declares (organizationId vs organizationsId). For each step this reports:
  unknown   parameters the step sends that the operation does not declare (name + in)
  missing   required path parameters the operation declares that the step does not send
With --write it repairs path parameters where the mapping is unambiguous: the step's unknown path parameters and the
operation's missing ones pair up one to one, either because there is exactly one of each or because their names agree
once case, punctuation and a plural `s` are ignored (organizationId -> organizationsId). The value is kept.
Credentials passed as parameters (Authorization, api_key, token, access_token ...) are not counted unless
--include-credentials: they belong in the security scheme, but runners accept them and they are not a broken call.
Usage: check-params.py [--json OUT] [--write] [--include-credentials] FILE_OR_DIR ...      (a directory is searched for */arazzo/*.y*ml)"""
import collections, glob, json, os, re, sys
import yaml

RAW = re.compile(r'^https://(?:raw\.githubusercontent\.com/api-evangelist/([^/]+)/(?:refs/heads/)?[^/]+|artifacts\.apis\.io/([^/]+))/(.+)$')
ALL = os.path.expanduser('~/Github/all')
_specs = {}

def spec_ops(path):
    if path not in _specs:
        ops = {}
        try:
            d = yaml.safe_load(open(path))
            for route, item in (d.get('paths') or {}).items():
                if not isinstance(item, dict): continue
                shared = [p for p in item.get('parameters') or [] if isinstance(p, dict)]
                for m, op in item.items():
                    if isinstance(op, dict) and op.get('operationId'):
                        ps = shared + [p for p in op.get('parameters') or [] if isinstance(p, dict)]
                        # A route placeholder IS a path parameter, declared or not: some generated specs (AWS Smithy
                        # conversions among them) template the route and never list it -- an OpenAPI defect, not the step's.
                        tpl = set(re.findall(r'\{([^}+]+)\+?\}', route))
                        ops[str(op['operationId'])] = ({(p.get('name'), p.get('in')) for p in ps if 'name' in p} | {(n, 'path') for n in tpl},
                                                       {p.get('name') for p in ps if p.get('in') == 'path' and p.get('required', True)} | tpl,
                                                       any('$ref' in p for p in ps))
        except Exception:
            ops = None
            # An apiKey security scheme declares its header/query parameter too (Palo Alto IoT: X-Key-Id).
            keys = {(sc.get('name'), sc.get('in')) for sc in ((d.get('components') or {}).get('securitySchemes') or {}).values()
                    if isinstance(sc, dict) and sc.get('type') == 'apiKey'}
            ops = {k: (v[0] | keys, v[1], v[2]) for k, v in ops.items()}
        _specs[path] = ops
    return _specs[path]

def resolve(doc_path, url):
    m = RAW.match(url)
    if m: return os.path.join(ALL, m.group(1) or m.group(2), m.group(3))
    if re.match(r'^[a-z]+://', url): return None
    return os.path.normpath(os.path.join(os.path.dirname(doc_path), url))

CRED = re.compile(r'^(auth\w*|api[-_]?key|apikey|key|token|access[-_]?token|bearer|x-api[-\w]*|x-auth[-\w]*|client[-_]?(id|secret)|secret)$', re.I)
# Vendor-named credentials (X-Vital-Api-Key, Api-Token, PRIVATE-TOKEN, Ocp-Apim-Subscription-Key, X-CSRF-Token) and the
# SigV4 / AWS protocol headers a signer or SDK sets (X-Amz-Date, X-Amz-Target). Idempotency-Key, If-Match and
# OpenAI-Beta are NOT here: those are real headers an operation should declare.
CRED_ANY = re.compile(r'(api[-_]?key|apikey|[-_]token$|subscription[-_]key|access[-_]key|logontoken|^fastly-key$|^session$|^cookie$|^x-amz-(date|content-sha256|security-token|target)$)', re.I)
# OpenAPI 3: header parameters named Accept, Content-Type or Authorization SHALL be ignored, so no operation can declare them.
RESERVED = {'accept', 'content-type', 'authorization'}
def is_cred(n, i='header'): return (i == 'header' and n.lower() in RESERVED) or bool(CRED.match(n) or (i == 'header' and CRED_ANY.search(n)))
INCLUDE_CREDENTIALS = False

def check(path):
    out = []
    d = yaml.safe_load(open(path))
    srcs = {s['name']: s for s in d.get('sourceDescriptions') or [] if isinstance(s, dict) and s.get('type', 'openapi') == 'openapi'}
    for w in d.get('workflows') or []:
        for st in (w or {}).get('steps') or []:
            oid = str(st.get('operationId') or '')
            if not oid: continue
            if oid.startswith('$sourceDescriptions.'):
                name, _, bare = oid[len('$sourceDescriptions.'):].partition('.')
                cands = [srcs[name]] if name in srcs else []
            else:
                bare, cands = oid, list(srcs.values())
            hit = None
            for s in cands:
                p = resolve(path, str(s.get('url', '')))
                ops = spec_ops(p) if p and os.path.exists(p) else None
                if ops and bare in ops: hit = ops[bare]; break
            if not hit or hit[2]: continue          # unresolved, or parameters behind $ref: not judged here
            declared, req_path, _ = hit
            given = {(p.get('name'), p.get('in')) for p in st.get('parameters') or [] if isinstance(p, dict) and 'in' in p}
            unknown = sorted(f'{n}({i})' for n, i in given - declared
                             if i in ('path', 'query', 'header') and (INCLUDE_CREDENTIALS or not is_cred(str(n), i)))
            missing = sorted(req_path - {n for n, i in given if i == 'path'})
            if unknown or missing:
                out.append({'step': st.get('stepId'), 'operationId': oid, 'unknown': unknown, 'missing': missing})
    return out

def norm(n):
    n = re.sub(r'[^a-z0-9]', '', n.lower())
    return re.sub(r's?(id|name|key)?$', '', n)

def pairing(unknown, missing):
    if len(unknown) == 1 and len(missing) == 1: return {unknown[0]: missing[0]}
    by = collections.defaultdict(list)
    for m in missing: by[norm(m)].append(m)
    out = {}
    for u in unknown:
        c = by.get(norm(u), [])
        if len(c) != 1: return None
        out[u] = c[0]
    return out if len(set(out.values())) == len(out) == len(missing) else None

def repair(path, problems, log):
    lines = open(path).read().splitlines(keepends=True); changed = False
    for pr in problems:
        unk = [re.match(r'(.*)\(path\)$', u).group(1) for u in pr['unknown'] if u.endswith('(path)')]
        if not unk or not pr['missing']: continue
        mp = pairing(unk, pr['missing'])
        if not mp: log['unpaired'] += 1; continue
        # The step's own definition is the list item `- stepId: X`; a bare `stepId: X` is a goto inside an action.
        s = next((i for i, l in enumerate(lines) if re.match(rf'^\s*- stepId:\s*[\'"]?{re.escape(str(pr["step"]))}[\'"]?\s*$', l)), None)
        if s is None: continue
        ind = len(lines[s]) - len(lines[s].lstrip(' -'))
        e = s + 1
        while e < len(lines) and not re.match(rf'^\s{{0,{max(ind - 2, 0)}}}- ', lines[e]) and not (lines[e].strip() and len(lines[e]) - len(lines[e].lstrip()) < ind): e += 1
        for i in range(s, e):
            m = re.match(r'^(\s*(?:- )?name:\s*)([\'"]?)([^\'"\s]+)\2\s*$', lines[i])
            if m and m.group(3) in mp and any(re.match(r'^\s*in:\s*path\s*$', lines[j]) for j in range(i + 1, min(i + 4, e))):
                lines[i] = f'{m.group(1)}{mp[m.group(3)]}\n'; changed = True; log['renamed'] += 1
    if changed:
        new = ''.join(lines); yaml.safe_load(new); open(path, 'w').write(new)
    return changed

def main():
    a = sys.argv[1:]; out = None; files = []; write = False
    while a:
        k = a.pop(0)
        if k == '--json': out = a.pop(0)
        elif k == '--write': write = True
        elif k == '--include-credentials':
            global INCLUDE_CREDENTIALS; INCLUDE_CREDENTIALS = True
        elif os.path.isdir(k): files += sorted(f for f in glob.glob(f'{k}/*/arazzo/*.y*ml') if not f.endswith('_index.yml'))
        else: files.append(k)
    res = {}
    for f in files:
        try: r = check(f)
        except Exception: continue
        if r: res[f] = r
    steps = sum(len(v) for v in res.values())
    print(f'{len(files)} documents, {len(res)} with parameter mismatches, {steps} steps')
    prov = collections.Counter(f.split('/all/')[-1].split('/')[0] for f in res)
    for p, n in prov.most_common(15): print(f'  {n:4d}  {p}')
    if out: json.dump(res, open(out, 'w'), indent=1)
    if write:
        log = collections.Counter(); files_changed = [f for f, pr in res.items() if repair(f, pr, log)]
        print(f'repaired {len(files_changed)} documents: {dict(log)}')
        if out: json.dump(files_changed, open(out + '.repaired', 'w'), indent=1)

if __name__ == '__main__':
    main()
