#!/usr/bin/env python3
"""Run every runner in tools.yml `runners:` against every execution case, with scripts/mock-petstore.py serving the API.
A case passes when the run's outcome matches `expect` (success | failure | any; contested = run and shown, not scored) AND the mock saw exactly `requests`,
in order, carrying the `match` values. Writes results/exec-<date>.json and docs/data/exec-latest.json.
Usage: run-exec.py [--tools TOOLDIR] [--only id,id] [--date YYYY-MM-DD]"""
import datetime, glob, json, os, re, shlex, subprocess, sys, time, urllib.request, yaml

SUITE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MOCK = 'http://127.0.0.1:4010'

def call(path, method='GET'):
    with urllib.request.urlopen(urllib.request.Request(MOCK + path, method=method, data=b'' if method == 'POST' else None), timeout=5) as r:
        return json.load(r)

def run(cmd, timeout=90):
    env = dict(os.environ, CI='1', NO_COLOR='1', FORCE_COLOR='0', TERM='dumb')
    p = subprocess.Popen(cmd, shell=True, cwd=SUITE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout); return p.returncode, re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', out)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(p.pid), 9); return -1, 'timeout'

def subset(want, got):
    if isinstance(want, dict):
        return isinstance(got, dict) and all(k in got and subset(v, got[k]) for k, v in want.items())
    return want == got

def judge(case, outcome, log):
    seen = [f"{r['method']} {r['path']}" for r in log]
    why = []
    if case['expect'] not in ('any', 'contested') and outcome != case['expect']: why.append(f"outcome {outcome}, expected {case['expect']}")
    if seen != case['requests']: why.append(f"requests {seen}, expected {case['requests']}")
    for i, want in (case.get('match') or {}).items():
        i = int(i)
        if i < len(log) and not subset(want, {k: log[i][k] for k in ('body', 'query', 'headers')}):
            why.append(f"request {i} carried {json.dumps({k: log[i][k] for k in want})}, expected {json.dumps(want)}")
    return not why, '; '.join(why)

def main():
    a = sys.argv[1:]; only = None; date = datetime.date.today().isoformat()
    tooldir = os.environ.get('ARAZZO_TOOLS', os.path.join(SUITE, '.tools'))
    while a:
        k = a.pop(0)
        if k == '--only': only = set(a.pop(0).split(','))
        elif k == '--date': date = a.pop(0)
        elif k == '--tools': tooldir = a.pop(0)
    env = {'suite': SUITE, 'node': f'{tooldir}/node', 'nb': f'{tooldir}/node/node_modules/.bin', 'py': f'{tooldir}/py/bin', 'go': f'{tooldir}/go'}
    runners = [t for t in yaml.safe_load(open(f'{SUITE}/tools.yml'))['runners'] if not only or t['id'] in only]
    cases = []
    for m in sorted(glob.glob(f'{SUITE}/cases/exec/*/manifest.yml')):
        for c in yaml.safe_load(open(m))['cases']:
            cases.append(dict(c, path=os.path.join(os.path.dirname(m), c['file']), file=os.path.relpath(os.path.join(os.path.dirname(m), c['file']), SUITE)))
    mock = subprocess.Popen([sys.executable, f'{SUITE}/scripts/mock-petstore.py', '4010'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try: call('/__log'); break
            except Exception: time.sleep(0.1)
        rows, meta = [], {}
        for t in runners:
            _, v = run(t['version_cmd'].format(**env))
            meta[t['id']] = {k: t.get(k, '') for k in ('name', 'repo', 'notes')} | {'version': v.strip().splitlines()[-1] if v.strip() else ''}
            for c in cases:
                call('/__reset', 'POST')
                kv = ' '.join(f'-i {shlex.quote(f"{k}={v}")}' for k, v in c['inputs'].items())
                code, out = run(t['cmd'].format(f=c['path'], workflow=c['workflow'], inputs=json.dumps(c['inputs']), inputs_kv=kv, **env))
                ok = re.search(t['success'], out) is not None if t.get('success') else code == 0
                outcome = 'timeout' if code == -1 else 'success' if ok else 'failure'
                log = call('/__log')
                passed, why = judge(c, outcome, log)
                rows.append({'tool': t['id'], 'case': c['id'], 'outcome': outcome, 'requests': [f"{r['method']} {r['path']}" for r in log],
                             'pass': passed, 'why': why, 'exit': code, 'tail': out.replace(SUITE, '.').strip()[-600:]})
                print(f"{t['id']:16s} {c['id']:36s} {outcome:8s} {'ok' if passed else 'MISS ' + why}")
    finally:
        mock.kill()
    doc = {'date': date, 'tools': meta, 'cases': [{k: c[k] for k in ('id', 'version', 'workflow', 'inputs', 'expect', 'requests', 'match', 'rule', 'spec', 'file', 'note') if k in c} for c in cases],
           'results': rows}
    os.makedirs(f'{SUITE}/results', exist_ok=True); os.makedirs(f'{SUITE}/docs/data', exist_ok=True)
    for p in (f'{SUITE}/results/exec-{date}.json', f'{SUITE}/docs/data/exec-latest.json'):
        json.dump(doc, open(p, 'w'), indent=1)
    print(f'{len(rows)} results, {len(runners)} runners, {len(cases)} cases')

if __name__ == '__main__':
    main()
