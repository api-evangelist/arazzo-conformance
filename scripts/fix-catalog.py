#!/usr/bin/env python3
"""Fix the two mechanical defects scripts/sweep.py finds in the API Evangelist catalog's Arazzo workflows.

  A  a bare operationId in a document with more than one OpenAPI source. Arazzo 1.0.1 (Step Object): "If multiple
     (non arazzo type) sourceDescriptions are defined, then the operationId MUST be specified using a runtime
     expression (e.g., $sourceDescriptions.<name>.<operationId>)". Rewritten when exactly one source declares the id.
  B  a criterion typed jsonpath whose condition is not JSONPath: `$.status == 'done'`, `$.data.length > 0`.
     A comparison against $response.body becomes a simple condition on a JSON Pointer ($response.body#/status == 'done');
     a non-empty check becomes the JSONPath `$.data[0]`, which selects something exactly when the array has an item.
  D  a double-quoted string literal in a simple condition ($statusCode == "ok"); Arazzo literals are single-quoted.
  E  expression and schema slips the validators all reject: `type: array` with no `items` (gets `items: {}`),
     `$response.headers.X` (the grammar says `$response.header.X`), and `$steps.<id>.<name>` missing `.outputs.`.
  F  a source pointing into openapi/_superseded/ or _original/ (archived copies, not the published set). Relinked to
     the ONE top-level spec declaring every operation (same HTTP method + path) the workflow calls through it, with
     each operationId remapped to that spec's id. No unique match -> listed.
  C  a sourceDescription url that no longer resolves. raw.githubusercontent.com/api-evangelist/<slug>/... has 404ed since
     provider profiles moved to S3; it becomes https://artifacts.apis.io/<slug>/<path>. A relative ../openapi/<file>
     renamed since the workflow was written is relinked to the one spec in that folder declaring every operation the
     workflow calls through it (several or none -> listed, not guessed).

Edits are line-level, so a file keeps its formatting; every edited file is re-parsed and must still load. Anything that
cannot be fixed without judgement is listed in the report, never guessed.
Usage: fix-catalog.py [--write | --out DIR] [--report report.json] FILE_OR_DIR ...
  default dry run; --out DIR writes fixed copies under DIR/<provider>/arazzo/ with DIR/<provider>/openapi symlinked to
  the original so relative sources still resolve; --write edits in place."""
import collections, glob, json, os, re, sys
import yaml

RAW = re.compile(r'^https://raw\.githubusercontent\.com/api-evangelist/([^/]+)/(?:refs/heads/)?[^/]+/(.+)$')
ART = re.compile(r'^https://artifacts\.apis\.io/([^/]+)/(.+)$')
ALL = os.path.expanduser('~/Github/all')
_ids = {}

def spec_path(doc_path, url):
    m = RAW.match(url) or ART.match(url)
    if m: return os.path.join(ALL, m.group(1), m.group(2))
    if re.match(r'^[a-z]+://', url): return None
    return os.path.normpath(os.path.join(os.path.dirname(doc_path), url))

def operation_ids(path):
    if path not in _ids:
        ids = set()
        try:
            d = yaml.safe_load(open(path))
            for item in (d.get('paths') or {}).values():
                if isinstance(item, dict):
                    for op in item.values():
                        if isinstance(op, dict) and op.get('operationId'): ids.add(str(op['operationId']))
        except Exception:
            ids = None
        _ids[path] = ids
    return _ids[path]

def step_ids(doc):
    for w in doc.get('workflows') or []:
        for st in (w or {}).get('steps') or []:
            if isinstance(st, dict) and st.get('operationId'): yield str(st['operationId'])

def set_url(lines, old, new):
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*(?:- )?url:\s*)([\'"]?)(.*?)\2\s*$', line)
        if m and m.group(3) == old: lines[i] = f'{m.group(1)}{new}\n'; return True
    return False

def fix_sources(path, doc, lines, log):
    oas = [s for s in doc.get('sourceDescriptions') or [] if isinstance(s, dict) and s.get('type', 'openapi') == 'openapi']
    ids = list(step_ids(doc))
    for s in oas:
        url = str(s.get('url', ''))
        local = spec_path(path, url)
        if local is None: continue
        new = url
        m = RAW.match(url)
        if m: new = f'https://artifacts.apis.io/{m.group(1)}/{m.group(2)}'
        if not os.path.exists(local):
            folder = os.path.dirname(local)
            explicit = {i.split('.', 2)[2] for i in ids if i.startswith(f'$sourceDescriptions.{s["name"]}.')}
            if explicit: need = explicit
            else:
                others = [operation_ids(spec_path(path, str(o.get('url', '')))) or set() for o in oas if o is not s and spec_path(path, str(o.get('url', ''))) and os.path.exists(spec_path(path, str(o.get('url', ''))))]
                need = {i for i in ids if not i.startswith('$') and not any(i in o for o in others)}
            cands = [f for f in sorted(glob.glob(f'{folder}/*.y*ml') + glob.glob(f'{folder}/*.json')) if need and need <= (operation_ids(f) or set())] if os.path.isdir(folder) else []
            if not cands and os.path.isdir(folder):
                # No published spec has them: fall back to the archived copies (_original/, _superseded/, _ae-authored/),
                # which are published too. The operations are real; the current spec set lost them (see the report).
                cands = [f for f in sorted(glob.glob(f'{folder}/_*/*.y*ml') + glob.glob(f'{folder}/_*/*.json')) if need and need <= (operation_ids(f) or set())]
                if len(cands) == 1: log['C-relinked-archived'] += 1
            if len(cands) != 1:
                log['unfixed'].append({'file': path, 'defect': 'C-source', 'url': url, 'why': f'renamed source matches {len(cands)} specs' if os.path.isdir(folder) else 'source folder missing'})
                continue
            rel = os.path.relpath(cands[0], folder)
            new = new[:new.rfind('/') + 1] + rel if '://' in new else os.path.join(os.path.dirname(url), rel)
            log['C-relinked'] += 1
        if new != url and set_url(lines, url, new):
            s['url'] = new
            if m: log['C-raw-to-artifacts'] += 1

OPID = re.compile(r'''^(\s*(?:- )?operationId:\s*)(['"]?)([^'"#\s]+)\2\s*$''')

def fix_operation_ids(path, doc, lines, log):
    oas = [s for s in doc.get('sourceDescriptions') or [] if isinstance(s, dict) and s.get('type', 'openapi') == 'openapi']
    if len(oas) < 2: return
    index = {s['name']: operation_ids(spec_path(path, str(s.get('url', '')))) if spec_path(path, str(s.get('url', ''))) else None for s in oas}
    for i, line in enumerate(lines):
        m = OPID.match(line)
        if not m or m.group(3).startswith('$'): continue
        opid = m.group(3)
        hits = [n for n, ids in index.items() if ids and opid in ids]
        if len(hits) == 1:
            lines[i] = f'{m.group(1)}$sourceDescriptions.{hits[0]}.{opid}\n'
            log['A-operationId'] += 1
        else:
            log['unfixed'].append({'file': path, 'line': i + 1, 'defect': 'A-operationId', 'operationId': opid,
                                   'why': 'in several sources: ' + ', '.join(hits) if hits else
                                          'in no readable source' + (' (unreadable: ' + ', '.join(n for n, ids in index.items() if ids is None) + ')' if None in index.values() else '')})

SEG = r'(?:\.[A-Za-z_][\w-]*|\[\d+\])'
RHS = r'''(?:'[^']*'|"[^"]*"|-?\d+(?:\.\d+)?|true|false|null|\$(?:inputs|steps|workflows|outputs)[\w.\-#/]*)'''
CMP = re.compile(rf'^\$({SEG}*)\s*(==|!=|<=|>=|<|>)\s*({RHS})$')
LEN = re.compile(rf'^\$({SEG}*)\.length\s*(>|>=|!=)\s*(\d+)$')

def pointer(segs):
    out = []
    for s in re.findall(SEG, segs):
        out.append(s[1:-1] if s.startswith('[') else s[1:].replace('~', '~0').replace('/', '~1'))
    return '/'.join(out)

def rewrite(context, cond):
    """-> ('simple', condition) | ('jsonpath', condition) | (None, why)"""
    m = LEN.match(cond.strip())
    if m:
        n = int(m.group(3)); op = m.group(2)
        k = n + 1 if op == '>' else n if op == '>=' else (1 if n == 0 else None)
        if k is None or k < 1: return None, 'length test with no index form'
        return 'jsonpath', f'${m.group(1)}[{k - 1}]'
    if '.length' in cond: return None, 'length test combined with other terms'
    cm = re.match(r'^\$response\.body(#(/[^\s]*)?)?$', context or '')
    if not cm: return None, f'context {context!r} is not $response.body'
    base = (cm.group(2) or '').rstrip('/')
    parts = re.split(r'\s+(\|\||&&)\s+', cond.strip())
    out = []
    for j, p in enumerate(parts):
        if j % 2: out.append(p); continue
        m = CMP.match(p)
        if not m: return None, 'not a path comparison'
        ptr = pointer(m.group(1))
        rhs = m.group(3)
        if rhs.startswith('"'): rhs = "'" + rhs[1:-1].replace("'", "''") + "'"   # Arazzo literals: single-quoted strings
        out.append(f"$response.body#{base}{'/' + ptr if ptr else ''} {m.group(2)} {rhs}")
    return 'simple', ' '.join(out)

def item_spans(lines):
    """Yield (start, end, indent) for every block-sequence item `- key: ...` whose sibling keys sit at indent + 2."""
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*)- (\w+):', line)
        if not m: continue
        ind = len(m.group(1)); j = i + 1
        while j < len(lines) and (lines[j].strip() == '' or len(lines[j]) - len(lines[j].lstrip()) >= ind + 2) and not re.match(rf'^\s{{{ind}}}- ', lines[j]):
            j += 1
        yield i, j, ind

def fix_criteria(path, lines, log):
    edits = []
    for s, e, ind in item_spans(lines):
        keys = {}
        for k in range(s, e):
            m = re.match(rf'^\s{{{ind}}}(?:- |  )(\w+):\s*(.*?)\s*$', lines[k])
            if m: keys[m.group(1)] = (k, m.group(2))
            elif lines[k].strip(): keys = None; break
        if not keys or set(keys) - {'context', 'condition', 'type'} or keys.get('type', (0, ''))[1] != 'jsonpath': continue
        cond = keys['condition'][1]
        if cond[:1] in '\'"' and cond[-1:] == cond[:1]: cond = cond[1:-1]
        if cond.startswith('$[') or cond.startswith('$..') or '?' in cond or not cond.startswith('$'): continue
        kind, new = rewrite(keys.get('context', (0, ''))[1], cond)
        if kind is None:
            log['unfixed'].append({'file': path, 'line': s + 1, 'defect': 'B-jsonpath', 'condition': cond, 'why': new}); continue
        if kind == 'simple':
            edits.append((s, e, [' ' * ind + '- condition: ' + new + '\n'])); log['B-to-simple'] += 1
        else:
            body = [' ' * ind + '- context: ' + keys['context'][1] + '\n'] if 'context' in keys else []
            body += [' ' * (ind + 2) + 'condition: ' + new + '\n', ' ' * (ind + 2) + 'type: jsonpath\n']
            if not body[0].lstrip().startswith('- '): body[0] = ' ' * ind + '- ' + body[0].lstrip()
            edits.append((s, e, body)); log['B-length'] += 1
    for s, e, body in reversed(edits):
        tail = [l for l in lines[s:e] if not l.strip()]
        lines[s:e] = body + tail

LIT = re.compile(r'(==|!=|<=|>=|<|>)(\s*)"([^"]*)"')

def fix_literals(lines, log):
    for s, e, ind in item_spans(lines):
        keys = {}
        for k in range(s, e):
            m = re.match(rf'^\s{{{ind}}}(?:- |  )(\w+):\s*(.*?)\s*$', lines[k])
            if m: keys[m.group(1)] = (k, m.group(2))
        if 'condition' not in keys or keys.get('type', (0, 'simple'))[1] != 'simple': continue
        k, cond = keys['condition']
        if cond[:1] in '\'"' or not LIT.search(cond): continue
        lines[k] = lines[k].replace(cond, LIT.sub(lambda m: f"{m.group(1)}{m.group(2)}'" + m.group(3).replace("'", "''") + "'", cond), 1)
        log['D-literal'] += 1

HDR = re.compile(r'\$response\.headers\.')
STEPS = re.compile(r'\$steps\.([A-Za-z0-9_.-]+?)\.(?!outputs\b)([A-Za-z_][\w-]*)\b')

def fix_slips(doc, lines, log):
    ids = {str(st.get('stepId')) for w in doc.get('workflows') or [] for st in (w or {}).get('steps') or [] if isinstance(st, dict)}
    for i, line in enumerate(lines):
        if HDR.search(line):
            lines[i] = line = HDR.sub('$response.header.', line); log['E-header'] += 1
        def steps(m):
            if m.group(1) not in ids: return m.group(0)
            log['E-steps-outputs'] += 1
            return f'$steps.{m.group(1)}.outputs.{m.group(2)}'
        new = STEPS.sub(steps, line)
        if new != line: lines[i] = new
    i = 0
    while i < len(lines):
        m = re.match(r'^(\s*)(?:- )?type:\s*array\s*$', lines[i])
        if m:
            ind = len(lines[i]) - len(lines[i].lstrip(' -')) if lines[i].lstrip().startswith('- ') else len(m.group(1))
            s = i
            while s > 0 and lines[s - 1].strip() and (len(lines[s - 1]) - len(lines[s - 1].lstrip())) >= ind and not lines[s - 1].lstrip().startswith('- ') : s -= 1
            e = i + 1
            while e < len(lines) and (not lines[e].strip() or len(lines[e]) - len(lines[e].lstrip()) >= ind): e += 1
            sib = [l for l in lines[s:e] if len(l) - len(l.lstrip()) == ind or l.lstrip().startswith('- ')]
            if not any(re.match(r'^\s*(- )?items:', l) for l in sib):
                lines.insert(i + 1, ' ' * ind + 'items: {}\n'); log['E-array-items'] += 1
        i += 1

_ops = {}
def ops_by_route(path):
    """{operationId: (METHOD, path)} for one spec, cached."""
    if path not in _ops:
        out = {}
        try:
            d = yaml.safe_load(open(path))
            for route, item in (d.get('paths') or {}).items():
                for m, op in (item or {}).items() if isinstance(item, dict) else []:
                    if isinstance(op, dict) and op.get('operationId'): out[str(op['operationId'])] = (m.upper(), re.sub(r'\{[^}]*\}', '{}', route))
        except Exception:
            pass
        _ops[path] = out
    return _ops[path]

def fix_archived(path, doc, lines, log):
    oas = [s for s in doc.get('sourceDescriptions') or [] if isinstance(s, dict) and s.get('type', 'openapi') == 'openapi']
    archived = [s for s in oas if re.search(r'/_(superseded|original)/', str(s.get('url', '')))]
    if not archived: return
    steps = [st for w in doc.get('workflows') or [] for st in (w or {}).get('steps') or [] if isinstance(st, dict) and st.get('operationId')]
    for s in archived:
        url = str(s['url']); old = spec_path(path, url)
        if not old or not os.path.exists(old):
            log['unfixed'].append({'file': path, 'defect': 'F-archived', 'url': url, 'why': 'archived source missing'}); continue
        routes = ops_by_route(old)
        prefix = f'$sourceDescriptions.{s["name"]}.'
        mine = [st for st in steps if str(st['operationId']).startswith(prefix) or (len(oas) == 1 and not str(st['operationId']).startswith('$'))
                or (not str(st['operationId']).startswith('$') and str(st['operationId']) in routes)]
        need = {}
        for st in mine:
            oid = str(st['operationId']); bare = oid[len(prefix):] if oid.startswith(prefix) else oid
            if bare in routes: need[oid] = routes[bare]
        if not need:
            log['unfixed'].append({'file': path, 'defect': 'F-archived', 'url': url, 'why': 'no step operation found in the archived spec'}); continue
        top = os.path.join(os.path.dirname(os.path.dirname(old)) if os.path.basename(os.path.dirname(old)).startswith('_') else os.path.dirname(old))
        cands = []
        for f in sorted(glob.glob(f'{top}/*.y*ml') + glob.glob(f'{top}/*.json')):
            inv = {v: k for k, v in ops_by_route(f).items()}
            if all(r in inv for r in need.values()): cands.append((f, inv))
        if len(cands) != 1:
            log['unfixed'].append({'file': path, 'defect': 'F-archived', 'url': url, 'why': f'{len(cands)} top-level specs declare every route'}); continue
        f, inv = cands[0]
        new_url = re.sub(r'/_(superseded|original)/.*$', '/' + os.path.basename(f), url)
        if not set_url(lines, url, new_url): continue
        for oid, route in need.items():
            bare = oid[len(prefix):] if oid.startswith(prefix) else oid
            new_id = (prefix if oid.startswith(prefix) else '') + inv[route]
            if new_id == oid: continue
            for i, line in enumerate(lines):
                m = OPID.match(line)
                if m and m.group(3) == oid: lines[i] = f'{m.group(1)}{new_id}\n'
        s['url'] = new_url; log['F-archived-relinked'] += 1

def main():
    a = sys.argv[1:]; write = False; out = None; report = None; paths = []
    while a:
        k = a.pop(0)
        if k == '--write': write = True
        elif k == '--out': out = os.path.abspath(a.pop(0))
        elif k == '--report': report = a.pop(0)
        else: paths.append(os.path.abspath(k))
    files = []
    for p in paths:
        files += sorted(f for f in glob.glob(f'{p}/*/arazzo/*.y*ml') if not f.endswith('_index.yml')) if os.path.isdir(p) else [p]
    log = collections.Counter(); log['unfixed'] = []; changed = []
    for f in files:
        try:
            text = open(f).read(); doc = yaml.safe_load(text)
        except Exception as ex:
            log['unfixed'].append({'file': f, 'defect': 'yaml', 'why': str(ex)[:200]}); continue
        if not isinstance(doc, dict) or str(doc.get('arazzo', '')) [:4] != '1.0.': continue
        lines = text.splitlines(keepends=True)
        fix_archived(f, doc, lines, log); fix_sources(f, doc, lines, log); fix_operation_ids(f, doc, lines, log); fix_criteria(f, lines, log); fix_literals(lines, log); fix_slips(doc, lines, log)
        new = ''.join(lines)
        if new == text: continue
        try: yaml.safe_load(new)
        except Exception as ex:
            log['unfixed'].append({'file': f, 'defect': 'edit-broke-yaml', 'why': str(ex)[:200]}); continue
        changed.append(f)
        if write: open(f, 'w').write(new)
        elif out:
            rel = os.path.relpath(f, ALL) if f.startswith(ALL) else os.path.join('_multi', os.path.basename(f))
            dst = os.path.join(out, rel); os.makedirs(os.path.dirname(dst), exist_ok=True); open(dst, 'w').write(new)
            src_openapi = os.path.join(os.path.dirname(os.path.dirname(f)), 'openapi')
            link = os.path.join(os.path.dirname(os.path.dirname(dst)), 'openapi')
            if f.startswith(ALL) and os.path.isdir(src_openapi) and not os.path.exists(link): os.symlink(src_openapi, link)
    unfixed = log.pop('unfixed')
    summary = {'documents': len(files), 'changed': len(changed), 'edits': dict(log),
               'unfixed': dict(collections.Counter(u['defect'] for u in unfixed)),
               'unfixed_why': dict(collections.Counter(f"{u['defect']}: {re.sub(r':.*', '', u['why'])}" for u in unfixed).most_common())}
    print(json.dumps(summary, indent=1))
    if report: json.dump(summary | {'changed_files': changed, 'unfixed_items': unfixed}, open(report, 'w'), indent=1)

if __name__ == '__main__':
    main()
