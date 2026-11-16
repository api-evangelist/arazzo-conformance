#!/usr/bin/env python3
"""Move AWS step parameters that belong in the request body into the step's requestBody.

AWS JSON-protocol (KMS, Kinesis, Forecast...) and Query-protocol (EC2, RDS, ELB...) operations take their input in a
request body: a JSON object, or an application/x-www-form-urlencoded form carrying Action and Version. Generated
workflows often send those members as query or path parameters instead, which the operation does not declare.

For each step that check-params.py flags, every unknown query/path parameter is looked up in the operation's request
body schema (through $ref and allOf). Only when EVERY unknown parameter is a member of that schema is the step
rewritten: the parameters become requestBody.payload entries (merged into an existing payload), with the spec's
content type. For a form body the schema's Action/Version consts are added. Anything else is left for a human and
listed with the reason.

Dry run by default; --write edits in place. Usage: aws-body-relocate.py [--write] FILE_OR_DIR ..."""
import importlib.util, json, os, sys
from ruamel.yaml import YAML

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('cp', os.path.join(HERE, 'check-params.py'))
cp = importlib.util.module_from_spec(spec); spec.loader.exec_module(cp)
import yaml as pyyaml

_docs = {}
def load(p):
    if p not in _docs: _docs[p] = pyyaml.safe_load(open(p))
    return _docs[p]

def deref(doc, s, depth=0):
    while isinstance(s, dict) and '$ref' in s and depth < 20:
        node = doc
        for part in s['$ref'].lstrip('#/').split('/'): node = node.get(part, {})
        s, depth = node, depth + 1
    return s if isinstance(s, dict) else {}

def props(doc, s, depth=0):
    """name -> schema for every property, flattening allOf; consts collected separately."""
    s = deref(doc, s); out = {}
    if depth > 10: return out
    for sub in s.get('allOf') or []: out.update(props(doc, sub, depth + 1))
    out.update(s.get('properties') or {})
    return out

def ci(members, n):
    """The member named n, ignoring case (KMS documents keyId, the model says KeyId)."""
    if n in members: return n
    hits = [m for m in members if m.lower() == n.lower()]
    return hits[0] if len(hits) == 1 else None

FLAT = cp.re.compile(r'^([A-Za-z]\w*)\.member\.(\d+)(?:\.([A-Za-z]\w*))?$')
def where(sd, members, n):
    """Where parameter n lives in the body, or None:
       ('set', key)               a member, case-insensitively;
       ('list', key)              EC2 Query names a list in the singular (InstanceId.N for InstanceIds);
       ('flat', key, i, sub)      the Query wire form Targets.member.1.Id -> Targets[0].Id."""
    k = ci(members, n)
    if k: return ('set', k)
    k = ci(members, n + 's')
    if k and deref(sd, members[k]).get('type') == 'array': return ('list', k)
    m = FLAT.match(n)
    if m:
        k = ci(members, m.group(1)); arr = deref(sd, members.get(k)) if k else {}
        if arr.get('type') == 'array':
            if not m.group(3): return ('flat', k, int(m.group(2)) - 1, None)
            sub = ci(props(sd, arr.get('items') or {}), m.group(3))
            if sub: return ('flat', k, int(m.group(2)) - 1, sub)
    return None

NV = cp.re.compile(r'^([\w.:-]+)=(.+)$')
def filters(v):
    """EC2 Filters is [{Name, Values}]: 'instance.group-id=$inputs.groupId' -> {Name: instance.group-id, Values: [$inputs.groupId]}."""
    if not isinstance(v, list) or not all(isinstance(x, str) and NV.match(x) for x in v): return v
    return [{'Name': NV.match(x).group(1), 'Values': [NV.match(x).group(2)]} for x in v]

def place(payload, t, v):
    if t[0] == 'set': payload[t[1]] = v
    elif t[0] == 'list':
        v = v if isinstance(v, list) else [v]
        payload[t[1]] = filters(v) if t[1] == 'Filters' else v
    else:
        arr = payload.setdefault(t[1], [])
        while len(arr) <= t[2]: arr.append({} if t[3] else None)
        if t[3]: arr[t[2]][t[3]] = v
        else: arr[t[2]] = v

def find_op(doc_path, d, oid):
    srcs = {s['name']: s for s in d.get('sourceDescriptions') or [] if isinstance(s, dict)}
    if oid.startswith('$sourceDescriptions.'):
        name, _, bare = oid[len('$sourceDescriptions.'):].partition('.')
        cands = [srcs[name]] if name in srcs else []
    else:
        bare, cands = oid, list(srcs.values())
    for s in cands:
        p = cp.resolve(doc_path, str(s.get('url', '')))
        if not p or not os.path.exists(p): continue
        sd = load(p)
        for route, item in (sd.get('paths') or {}).items():
            for m, op in (item or {}).items():
                if isinstance(op, dict) and str(op.get('operationId')) == bare: return sd, op
    return None, None

def plan(path, problems):
    d = load(path); edits = []; skipped = []
    steps = {st.get('stepId'): st for w in d.get('workflows') or [] for st in (w or {}).get('steps') or []}
    for pr in problems:
        st = steps.get(pr['step'])
        moving = [u for u in pr['unknown'] if not u.endswith('(header)')]
        if not st or not moving: continue
        if pr['missing']: skipped.append((pr['step'], 'also missing a required path parameter')); continue
        sd, op = find_op(path, d, pr['operationId'])
        rb = deref(sd, (op or {}).get('requestBody'))
        content = rb.get('content') or {}
        if not content: skipped.append((pr['step'], 'operation has no request body')); continue
        ctype = next(iter(content)); schema = content[ctype].get('schema') or {}
        members = props(sd, schema)
        names = [u.rsplit('(', 1)[0] for u in moving]
        target = {n: where(sd, members, n) for n in names}
        absent = [n for n, t in target.items() if not t]
        if absent: skipped.append((pr['step'], f'not body members: {absent}')); continue
        consts = {k: v['const'] for k, v in members.items() if isinstance(v, dict) and 'const' in v} if 'form' in ctype else {}
        edits.append({'step': pr['step'], 'contentType': ctype, 'move': names, 'consts': consts, 'target': target})
    return edits, skipped

def apply(path, edits):
    y = YAML(); y.preserve_quotes = True; y.width = 4096; y.indent(mapping=2, sequence=2, offset=0)
    doc = y.load(open(path))
    by = {e['step']: e for e in edits}
    for w in doc.get('workflows') or []:
        for st in w.get('steps') or []:
            e = by.get(st.get('stepId'))
            if not e: continue
            keep, moved = [], {}
            for p in st.get('parameters') or []:
                if isinstance(p, dict) and p.get('in') in ('query', 'path') and p.get('name') in e['move']:
                    place(moved, e['target'][p['name']], p.get('value'))
                else: keep.append(p)
            payload = {**e['consts'], **{k: v for k, v in moved.items() if k not in e['consts']}}
            rb = st.get('requestBody')
            if rb and isinstance(rb.get('payload'), dict):
                for k, v in payload.items(): rb['payload'].setdefault(k, v)
            else:
                keys = list(st.keys())
                at = keys.index('parameters') if 'parameters' in keys else keys.index('operationId') + 1 if 'operationId' in keys else len(keys)
                st.insert(at, 'requestBody', {'contentType': e['contentType'], 'payload': payload})
            if keep: st['parameters'] = keep
            elif 'parameters' in st: del st['parameters']
    with open(path, 'w') as f: y.dump(doc, f)

def main():
    a = sys.argv[1:]; write = '--write' in a; a = [x for x in a if x != '--write']
    files = []
    for x in a: files += cp.glob.glob(os.path.join(x, '*/arazzo/*.y*ml')) if os.path.isdir(x) else [x]
    report = {'rewritten': {}, 'skipped': {}}
    for f in sorted(files):
        probs = cp.check(f)
        if not probs: continue
        edits, skipped = plan(f, probs)
        if edits:
            report['rewritten'][f] = edits
            if write: apply(f, edits)
        if skipped: report['skipped'][f] = skipped
    n = sum(len(v) for v in report['rewritten'].values())
    print(f"{len(report['rewritten'])} documents, {n} steps {'rewritten' if write else 'would be rewritten'}; "
          f"{sum(len(v) for v in report['skipped'].values())} steps skipped")
    json.dump(report, sys.stdout if '--json' not in a else open(a[a.index('--json') + 1], 'w'), indent=1)

if __name__ == '__main__': main()
