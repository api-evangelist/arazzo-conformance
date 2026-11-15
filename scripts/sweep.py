#!/usr/bin/env python3
"""Run real-world Arazzo documents through the suite's validators and tally what they object to.
The validation tier asks whether a tool is right; this asks what the tools say about documents in the wild.
Usage: sweep.py --tools DIR [--only spectral,usearazzo] [--jobs 10] [--out sweep.json] FILE_OR_DIR ...
A directory is searched for */arazzo/*.yml (catalog layout) and *.arazzo.yaml; `_index.yml` is skipped.
Writes per-file verdicts plus an error-class tally: numbers, quoted strings and positions are folded out of each
diagnostic line so the same rule groups together across files."""
import collections, glob, json, os, re, sys
from concurrent.futures import ThreadPoolExecutor
import yaml
from run import SUITE, run

def files(args):
    for a in args:
        if os.path.isdir(a):
            yield from sorted(set(glob.glob(f'{a}/*/arazzo/*.yml') + glob.glob(f'{a}/**/*.arazzo.yaml', recursive=True)) - set(glob.glob(f'{a}/*/arazzo/_index.yml')))
        else:
            yield a

def classify(line):
    line = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', line).strip()
    line = re.sub(r'^\s*\d+[.:)]\s*', '', line)
    line = re.sub(r'\[?\d+:\d+\]?', '', line)
    line = re.sub(r'"[^"]*"|\'[^\']*\'|`[^`]*`', 'X', line)
    return re.sub(r'\d+', 'N', line).strip()

NOISE = re.compile(r'^(Validating|Error: arazzo document|❌|✖ \d+ problem|No results|$|\s*\^|Woohoo|validated in|/|You have|Run `)', re.I)

def main():
    a = sys.argv[1:]; only = None; jobs = 10; out = None; paths = []
    tooldir = os.environ.get('ARAZZO_TOOLS', os.path.join(SUITE, '.tools'))
    while a:
        k = a.pop(0)
        if k == '--only': only = set(a.pop(0).split(','))
        elif k == '--tools': tooldir = os.path.abspath(a.pop(0))
        elif k == '--jobs': jobs = int(a.pop(0))
        elif k == '--out': out = a.pop(0)
        else: paths.append(os.path.abspath(k))
    env = {'suite': SUITE, 'node': f'{tooldir}/node', 'nb': f'{tooldir}/node/node_modules/.bin', 'py': f'{tooldir}/py/bin', 'go': f'{tooldir}/go',
           'unode': f'{tooldir}/node-usearazzo', 'ub': f'{tooldir}/node-usearazzo/node_modules/.bin'}
    tools = [t for t in yaml.safe_load(open(f'{SUITE}/tools.yml'))['tools']
             if t.get('role') == 'validates' and not t.get('skip') and (not only or t['id'] in only)]
    fs = list(files(paths))
    print(f'{len(fs)} documents x {len(tools)} tools', flush=True)
    rows, tally = [], {t['id']: collections.Counter() for t in tools}
    for t in tools:
        def one(f):
            code, o = run(t['cmd'].format(f=f, schema='', **env), timeout=120)
            o = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', o)
            if code == -1: v = 'timeout'
            elif code == 127: v = 'error'   # the command itself is missing: a harness fault, never a verdict
            elif t.get('error') and re.search(t['error'], o): v = 'error'
            elif t.get('unsupported') and re.search(t['unsupported'], o): v = 'unsupported'
            else: v = 'valid' if code == 0 else 'invalid'
            return f, v, o
        n = collections.Counter()
        with ThreadPoolExecutor(jobs) as ex:
            for f, v, o in ex.map(one, fs):
                n[v] += 1
                classes = sorted({classify(l) for l in o.splitlines() if not NOISE.match(l.strip())}) if v == 'invalid' else []
                for c in classes: tally[t['id']][c] += 1
                rows.append({'tool': t['id'], 'file': f, 'verdict': v, 'classes': classes, 'tail': o.strip()[-800:] if v != 'valid' else ''})
        print(f"{t['id']:18s} " + ' '.join(f'{k}={v}' for k, v in sorted(n.items())), flush=True)
    doc = {'documents': len(fs), 'tools': [t['id'] for t in tools], 'results': rows,
           'classes': {t: [{'class': c, 'files': k} for c, k in cnt.most_common()] for t, cnt in tally.items()}}
    if out: json.dump(doc, open(out, 'w'), indent=1)
    for t, cnt in tally.items():
        print(f'\n{t}: top classes (files)')
        for c, k in cnt.most_common(12): print(f'  {k:6d}  {c[:150]}')

if __name__ == '__main__':
    main()
