"""Standalone non-trading preview. Standard library only; no HOPE imports."""
import argparse
import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import re
import stat
import sys


FILES = {'server.py', 'index.html', 'manage.py', 'deploy.sh', 'rollback.sh'}
PORT = 8790


def verify(root, commit):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('STAGING_COMMIT_INVALID')
    manifest_path = root / 'MANIFEST.json'
    manifest_info = manifest_path.lstat()
    if not stat.S_ISREG(manifest_info.st_mode) or not 0 < manifest_info.st_size <= 262144:
        raise ValueError('STAGING_MANIFEST_INVALID')
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    canonical_manifest = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode() + b'\n'
    if manifest_bytes != canonical_manifest:
        raise ValueError('STAGING_MANIFEST_INVALID')
    if (set(manifest) != {'schema', 'commit', 'files', 'scope'}
            or manifest['schema'] != 'hope.nontrading-staging.v1'
            or manifest['commit'] != commit or manifest['scope'] != 'PREVIEW_ONLY_NO_DATABASE'
            or type(manifest['files']) is not dict or set(manifest['files']) != FILES
            or {p.name for p in root.iterdir()} != FILES | {'MANIFEST.json'}):
        raise ValueError('STAGING_MANIFEST_INVALID')
    for name, expected in manifest['files'].items():
        path = root / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_size > 262144
                or type(expected) is not str
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError('STAGING_PAYLOAD_MISMATCH')
    return manifest


def seal_runtime():
    # The preview accepts inbound loopback HTTP, but can neither connect to a
    # database/broker nor spawn an execution worker. This is defense in depth,
    # not a sandbox against a compromised interpreter or administrative root.
    def audit(event, args):
        if event in {'socket.connect', 'subprocess.Popen', 'os.system', 'os.exec',
                     'os.posix_spawn', 'os.fork', 'os.forkpty', 'sqlite3.connect', 'ctypes.dlopen'}:
            raise PermissionError('STAGING_EXECUTION_DENIED')
        if event == 'open':
            mode = args[1]
            flags = args[2]
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                raise PermissionError('STAGING_WRITE_DENIED')
    sys.addaudithook(audit)


def handler(commit, page):
    class Handler(BaseHTTPRequestHandler):
        timeout = 3
        def do_GET(self):
            if self.headers.get('Host') not in {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}:
                self.send_error(403)
                return
            if self.path == '/':
                body, content_type = page, 'text/html; charset=utf-8'
            elif self.path == '/status.json':
                body = json.dumps(dict(schema='hope.nontrading-preview.v1',
                    release_commit=commit, scope='PREVIEW_ONLY_NO_DATABASE',
                    paper_enabled=False, live_enabled=False, execution_authorized=False,
                    database='NOT_CONNECTED', broker='NOT_INCLUDED',
                    independent_acceptance='UNVERIFIED', operational_readiness='BLOCKED')).encode()
                content_type = 'application/json'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_error(405)

        def log_message(self, *args):
            pass
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    verify(root, args.commit)
    page = (root / 'index.html').read_bytes()
    seal_runtime()
    if args.check:
        print(json.dumps({'scope': 'PREVIEW_ONLY_NO_DATABASE', 'execution_authorized': False}))
        return
    with HTTPServer(('127.0.0.1', PORT), handler(args.commit, page)) as server:
        server.serve_forever(poll_interval=0.25)


if __name__ == '__main__':
    main()
