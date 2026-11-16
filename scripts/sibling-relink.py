"""Point workflows still on an archived (_superseded/_original) source at the SIBLING provider whose current specs now
hold those operations (Kin, 2026-11-15: "point vendor maps at siblings"). Vendor-spec rollouts scoped each provider's
contract (microsoft-entra = Graph identity paths only), so /applications etc. moved to e.g. microsoft-graph.

Per archived source: every route the workflow calls through it must be declared by ONE current spec of ONE sibling
(same vendor family; exact or version-stripped method+path, unique within that spec). Several qualifying siblings ->
prefer a vendor-model member (has smithy/, discovery/ or a fetched _original), else skip. The source url becomes
https://artifacts.apis.io/<sibling>/openapi/<file> and operationIds are remapped. Dry run unless --write."""
import collections, glob, json, os, re, sys, yaml, importlib.util
spec = importlib.util.spec_from_file_location('fc', os.path.expanduser('~/Github/api-evangelist/arazzo-conformance/scripts/fix-catalog.py'))
fc = importlib.util.module_from_spec(spec); spec.loader.exec_module(fc)
A = os.path.expanduser('~/Github/all'); WRITE = '--write' in sys.argv
FAM = re.compile(r'^(microsoft|azure|google|youtube|apigee|sharepoint|github|android|firebase|gmail|looker|power-bi|active-directory|office)')
def family(s): return 'microsoft' if re.match(r'^(microsoft|azure|sharepoint|github|power-bi|active-directory|office)', s) else 'google'
def vendor_member(s): return any(os.path.isdir(f'{A}/{s}/{d}') for d in ('smithy', 'discovery')) or bool(glob.glob(f'{A}/{s}/openapi/_original/*.fetch.json'))
# index: route -> [(sibling, spec file, opId)] over current top-level specs of the families
idx, uidx = collections.defaultdict(list), collections.defaultdict(list)
for f in glob.glob(A + '/*/openapi/*.y*ml') + glob.glob(A + '/*/openapi/*.json'):
    s = f.split('/all/')[1].split('/')[0]
    if not FAM.match(s): continue
    for oid, rt in fc.ops_by_route(f).items():
        idx[rt].append((s, f, oid)); uidx[fc.unversioned(rt)].append((s, f, oid))
reg = json.load(open('archived-only-ops-by-provider.json'))
REJECT = {('google-quantum-ai', 'google-android-notification-configuration-workflow.yml'),
          ('google-workspace', 'youtube-search-channel-subscribe-workflow.yml'),
          ('google-cloud-api-gateway', 'apigee-registry-catalog-version-workflow.yml')}
log = collections.Counter(); changed = []; detail = []
for slug, files in sorted(reg.items()):
    if not FAM.match(slug): continue
    for path in files:
        if not os.path.exists(path): continue
        text = open(path).read(); doc = yaml.safe_load(text); lines = text.splitlines(keepends=True)
        oas = [s for s in doc.get('sourceDescriptions') or [] if isinstance(s, dict) and s.get('type', 'openapi') == 'openapi']
        steps = [st for w in doc.get('workflows') or [] for st in (w or {}).get('steps') or [] if isinstance(st, dict) and st.get('operationId')]
        touched = False
        for s in oas:
            url = str(s.get('url', ''))
            if not re.search(r'/_(superseded|original)/', url): continue
            old = fc.spec_path(path, url)
            if not old or not os.path.exists(old): continue
            routes = fc.ops_by_route(old); prefix = f'$sourceDescriptions.{s["name"]}.'
            need = {}
            for st in steps:
                oid = str(st['operationId']); bare = oid[len(prefix):] if oid.startswith(prefix) else (oid if len(oas) == 1 or not oid.startswith('$') else None)
                if bare and bare in routes: need[oid] = routes[bare]
            if not need: continue
            per = collections.defaultdict(dict)       # (sibling, spec) -> {oid: new opId}
            for oid, rt in need.items():
                hits = [h for h in idx.get(rt, []) if h[0] != slug and family(h[0]) == family(slug)] or \
                       [h for h in uidx.get(fc.unversioned(rt), []) if h[0] != slug and family(h[0]) == family(slug)]
                for sib, f, new in hits: per[(sib, f)][oid] = new
            full = [(k, v) for k, v in per.items() if len(v) == len(need) and len(set(v.values())) == len(v)]
            if len(full) > 1:
                vm = [x for x in full if vendor_member(x[0][0])]
                full = vm if len({x[0][0] for x in vm}) == 1 and len(vm) == 1 else ([] if len(vm) != 1 else vm)
            if len(full) != 1:
                log['no unique sibling'] += 1; detail.append((path, url, sorted({k[0] for k, _ in per.items()})[:4])); continue
            (sib, f), mp = full[0]
            # Reviewed by hand 2026-11-15: matches on generic routes (/v1/{}, POST /subscriptions) or a different
            # service with a look-alike route (Apigee Registry vs Cloud API Gateway). Not the same operations.
            if (sib, os.path.basename(path)) in REJECT: log['rejected on review'] += 1; continue
            new_url = f'https://artifacts.apis.io/{sib}/openapi/{os.path.basename(f)}'
            if not fc.set_url(lines, url, new_url): continue
            for oid, new in mp.items():
                nid = (prefix if oid.startswith('$') else ('' if len(oas) == 1 else prefix)) + new
                for i, line in enumerate(lines):
                    m = fc.OPID.match(line)
                    if m and m.group(3) == oid: lines[i] = f'{m.group(1)}{nid}\n'
            touched = True; log[f'relinked -> {sib}'] += 1
        if touched:
            new = ''.join(lines); yaml.safe_load(new); changed.append(path)
            if WRITE: open(path, 'w').write(new)
print(len(changed), 'files'); [print(f'  {n:4d} {k}') for k, n in log.most_common()]
json.dump({'changed': changed, 'skipped': detail}, open('sibling-report.json', 'w'), indent=1)
