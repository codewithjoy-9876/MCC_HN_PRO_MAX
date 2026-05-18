#!/usr/bin/env python3
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.getenv('PORT', '8080'))
BASE = os.path.dirname(os.path.abspath(__file__))
STATE_JSON = os.path.join(BASE, 'presence_state.json')


def load_state() -> dict:
    fallback = {
        'ok': True,
        'service': 'HN_PRO_MAX',
        'mode': 'afk_presence',
        'joined': False,
        'connected_once': False,
        'source': 'run_forever health server',
    }
    try:
        with open(STATE_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data['source'] = 'run_forever health server'
        return data
    except Exception:
        return fallback


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ['/', '/health', '/healthz']:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'not found')
            return
        body = json.dumps(load_state()).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.serve_forever()
