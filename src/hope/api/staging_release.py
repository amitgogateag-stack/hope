"""Build an exact allow-listed, dependency-free non-trading preview ZIP.

This separate format is not an approved PAPER image or independent attestation.
It consumes immutable Git blobs without hooks, pip or application imports.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

FILES = ('server.py', 'index.html', 'manage.py', 'deploy.sh', 'rollback.sh')


def build(repo, commit, destination):
    if type(commit) is not str or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('STAGING_COMMIT_INVALID')
    def git(*args):
        clean = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        clean.update(GIT_CONFIG_NOSYSTEM='1', HOME='/nonexistent', GIT_CONFIG_GLOBAL='/dev/null', GIT_NO_REPLACE_OBJECTS='1')
        return subprocess.run(['git', '--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
            '-C', str(repo), *args], check=True, capture_output=True, timeout=30, env=clean).stdout
    if git('rev-parse', '--verify', commit + '^{commit}').decode().strip() != commit:
        raise ValueError('STAGING_COMMIT_INVALID')
    files = {}
    for name in FILES:
        path = 'deploy/staging/' + name
        mode = git('ls-tree', commit, '--', path).split()[0]
        if mode != b'100644':
            raise ValueError('STAGING_SOURCE_MODE_INVALID')
        files[name] = git('show', commit + ':' + path)
        if len(files[name]) > 262144:
            raise ValueError('STAGING_SOURCE_SIZE')
    manifest = dict(schema='hope.nontrading-staging.v1', commit=commit,
        scope='PREVIEW_ONLY_NO_DATABASE',
        files={k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())})
    files['MANIFEST.json'] = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode() + b'\n'
    with destination.open('xb') as output:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as z:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100444 << 16
                z.writestr(info, data)
    return {'commit': commit, 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'bytes': destination.stat().st_size, 'execution_authorized': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.repo, args.commit, args.output), sort_keys=True))


if __name__ == '__main__':
    main()
