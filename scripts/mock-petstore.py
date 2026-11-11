#!/usr/bin/env python3
"""The petstore the execution cases run against (cases/ae/sources/petstore.openapi.yaml), with no dependencies.
Every request is logged so a case can assert which calls a runner made, in what order, with what values.
  GET  /__log    the requests since the last reset (JSON)     POST /__reset   clear pets + log
Behaviour a case can lean on: ids are pet-1, pet-2, ... in creation order; a pet named "flaky" answers 404 to its
first GET and 200 after; GET of an unknown id is 404.
Usage: mock-petstore.py [port]   (default 4010)"""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

STATE = {'pets': {}, 'log': [], 'gets': {}}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body=None):
        data = b'' if body is None else json.dumps(body).encode()
        self.send_response(code)
        if body is not None: self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def record(self, body):
        u = urlsplit(self.path)
        if u.path.startswith('/__'): return
        STATE['log'].append({'method': self.command, 'path': u.path, 'query': {k: v[0] for k, v in parse_qs(u.query).items()},
                             'headers': {k.lower(): v for k, v in self.headers.items() if k.lower().startswith('x-')}, 'body': body})

    def do_GET(self):
        u = urlsplit(self.path); self.record(None)
        if u.path == '/__log': return self.send(200, STATE['log'])
        if u.path == '/pets':
            limit = int(parse_qs(u.query).get('limit', ['100'])[0])
            return self.send(200, list(STATE['pets'].values())[:limit])
        if u.path.startswith('/pets/'):
            pid = u.path.split('/')[2]; pet = STATE['pets'].get(pid)
            if not pet: return self.send(404, {'message': 'not found'})
            STATE['gets'][pid] = STATE['gets'].get(pid, 0) + 1
            if pet['name'] == 'flaky' and STATE['gets'][pid] == 1: return self.send(404, {'message': 'not yet'})
            return self.send(200, pet)
        self.send(404, {'message': 'no route'})

    def do_POST(self):
        u = urlsplit(self.path)
        raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        try: body = json.loads(raw) if raw else None
        except ValueError: body = raw.decode(errors='replace')
        self.record(body)
        if u.path == '/__reset':
            STATE.update(pets={}, log=[], gets={}); return self.send(200, {'ok': True})
        if u.path == '/pets':
            if not isinstance(body, dict) or not body.get('name'): return self.send(400, {'message': 'name is required'})
            pid = f'pet-{len(STATE["pets"]) + 1}'
            STATE['pets'][pid] = {'id': pid, 'name': body['name'], 'status': body.get('status', 'available')}
            return self.send(201, STATE['pets'][pid])
        self.send(404, {'message': 'no route'})

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4010
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
