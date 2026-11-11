#!/usr/bin/env python3
"""Run every tool in tools.yml against every case and write results/<date>.json + docs/data/latest.json.
Usage: run.py [--tools TOOLDIR] [--only id,id] [--date YYYY-MM-DD]
TOOLDIR holds node/ (npm installs), py/ (venv) and go/ (go install bin). See README for the install lines."""
import datetime, glob, json, os, re, shutil, subprocess, sys, yaml

SUITE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def load_cases():
    cases = []
    for m in sorted(glob.glob(f'{SUITE}/cases/ae/*/manifest.yml')):
        d = os.path.dirname(m)
        for c in yaml.safe_load(open(m))['cases']:
            cases.append(dict(c, suite='ae', path=os.path.join(d, c['file'])))
    # OAI's own schema tests: pass/ = valid, fail/ = invalid. A characterization-* case passes the schema
    # on purpose while breaking a rule stated in prose, so for a validator the right answer is invalid.
    for f in sorted(glob.glob(f'{SUITE}/cases/oai/*/*/*.yaml')):
        version, kind = f.split('/')[-3], f.split('/')[-2]
        name = os.path.basename(f).replace('.arazzo.yaml', '').replace('.yaml', '')
        charac = name.startswith('characterization-')
        cases.append({'id': f'oai-{version}-{kind}-{name}', 'suite': 'oai', 'version': version, 'path': f, 'file': os.path.relpath(f, SUITE),
                      'expect': 'invalid' if (kind == 'fail' or charac) else 'valid', 'level': 'semantic' if charac else 'schema',
                      'rule': name.replace('-', ' '), 'spec': f'https://spec.openapis.org/arazzo/v{version}.html'})
    return cases

def run(cmd, timeout=60):
    env = dict(os.environ, CI='1', NO_COLOR='1', FORCE_COLOR='0', TERM='dumb')
    p = subprocess.Popen(cmd, shell=True, cwd=SUITE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout); return p.returncode, out
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(p.pid), 9); return -1, 'timeout'

def main():
    a = sys.argv[1:]; only = None; date = datetime.date.today().isoformat()
    tooldir = os.environ.get('ARAZZO_TOOLS', os.path.join(SUITE, '.tools'))
    while a:
        k = a.pop(0)
        if k == '--only': only = set(a.pop(0).split(','))
        elif k == '--date': date = a.pop(0)
        elif k == '--tools': tooldir = a.pop(0)
    env = {'suite': SUITE, 'node': f'{tooldir}/node', 'nb': f'{tooldir}/node/node_modules/.bin', 'py': f'{tooldir}/py/bin', 'go': f'{tooldir}/go'}
    shutil.copy(f'{SUITE}/scripts/schema-check.mjs', env['node'])
    tools = [t for t in yaml.safe_load(open(f'{SUITE}/tools.yml'))['tools'] if not only or t['id'] in only]
    cases = load_cases()
    rows, meta = [], {}
    for t in tools:
        _, v = run(t['version_cmd'].format(**env))
        meta[t['id']] = {k: t.get(k, '') for k in ('name', 'repo', 'role', 'notes')} | {'version': v.strip().splitlines()[-1] if v.strip() else ''}
        for c in cases:
            schema = f"{SUITE}/schemas/{c['version']}/schema.yaml"
            code, out = run(t['cmd'].format(f=c['path'], schema=schema, **env))
            out = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', out).strip()
            if code == -1: verdict = 'timeout'
            elif t.get('error') and re.search(t['error'], out): verdict = 'error'
            elif t.get('unsupported') and re.search(t['unsupported'], out): verdict = 'unsupported'
            else: verdict = 'valid' if code == 0 else 'invalid'
            rows.append({'tool': t['id'], 'case': c['id'], 'suite': c['suite'], 'version': c['version'], 'level': c['level'], 'expect': c['expect'],
                         'verdict': verdict, 'pass': verdict == c['expect'], 'exit': code, 'tail': out.replace(SUITE, '.')[-600:]})
            print(f"{t['id']:18s} {c['id'][:60]:60s} expect={c['expect']:7s} got={verdict:7s} {'ok' if verdict == c['expect'] else 'MISS'}")
    doc = {'date': date, 'tools': meta, 'cases': [{k: c[k] for k in ('id', 'suite', 'version', 'level', 'expect', 'rule', 'spec', 'file', 'note') if k in c} for c in cases], 'results': rows}
    os.makedirs(f'{SUITE}/results', exist_ok=True); os.makedirs(f'{SUITE}/docs/data', exist_ok=True)
    for p in (f'{SUITE}/results/{date}.json', f'{SUITE}/docs/data/latest.json'):
        json.dump(doc, open(p, 'w'), indent=1)
    print(f'{len(rows)} results, {len(tools)} tools, {len(cases)} cases')

if __name__ == '__main__':
    main()
