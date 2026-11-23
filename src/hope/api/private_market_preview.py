"""Reproducible static preview assets and private market projection export.

Build provenance is not runtime, market truth or financial admission authority.
There is no database, broker, scheduler, strategy or credential access.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile

FILES = {
    'index.html': 'docs/dashboard/preview.html',
    'market.html': 'docs/dashboard/market.html',
    'preview_install.py': 'deploy/operations/private_market_preview_install.py',
}


def build_assets(repository: Path, commit: str) -> bytes:
    """Read only fixed Git objects, independent of dirty files/checkout newlines."""
    if not isinstance(commit, str) or re.fullmatch('[0-9a-f]{40}', commit) is None:
        raise ValueError('PREVIEW_COMMIT_INVALID')
    blobs = {}
    for name, source in FILES.items():
        entry = subprocess.run(['git', 'ls-tree', '-z', commit, '--', source], cwd=repository,
                               check=True, capture_output=True, timeout=15).stdout
        if (entry.split(b' ', 1)[0] not in {b'100644', b'100755'}
                or not entry.endswith(b'\t' + source.encode() + b'\0')):
            raise ValueError('PREVIEW_GIT_ASSET_NOT_REGULAR')
        # cat-file reads a Git object unambiguously. `show SHA:path` may first
        # stat that expression against a deep Windows working directory.
        result = subprocess.run(['git', 'cat-file', 'blob', commit + ':' + source], cwd=repository,
                                check=True, capture_output=True, timeout=15)
        if not 0 < len(result.stdout) <= 65536:
            raise ValueError('PREVIEW_ASSET_SIZE_INVALID')
        blobs[name] = result.stdout
    manifest = {
        'schema': 'hope.private-static-preview-assets.v1', 'source_commit': commit,
        'scope': 'PRIVATE_STATIC_PREVIEW_ONLY', 'execution_authorized': False,
        'runtime_verified': False, 'production_admission': False,
        'files': {n: {'sha256': hashlib.sha256(b).hexdigest(), 'bytes': len(b)}
                  for n, b in sorted(blobs.items())},
    }
    blobs['ASSETS.json'] = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(blobs.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            archive.writestr(info, data)
    return stream.getvalue()


def project_capture(payload: bytes, commit: str, now: datetime) -> bytes:
    from hope.api.market_observation import market_observation
    value = market_observation(payload, expected_commit=commit, now=now)
    # This projection alone is suitable for the existing static preview server.
    # Raw captures/receipts never enter its served directory.
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    build = modes.add_parser('build-assets')
    build.add_argument('--repository', type=Path, required=True)
    build.add_argument('--commit', required=True)
    build.add_argument('--output', type=Path, required=True)
    export = modes.add_parser('project-capture')
    export.add_argument('--capture', type=Path, required=True)
    export.add_argument('--commit', required=True)
    export.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'build-assets':
        data = build_assets(args.repository, args.commit)
    else:
        from hope.api.market_observation import MAX_BYTES
        from hope.infrastructure.durable_file import bounded_regular_read
        data = project_capture(bounded_regular_read(args.capture, MAX_BYTES), args.commit,
                               datetime.now(timezone.utc))
    with args.output.open('xb') as output:
        output.write(data)
    print(json.dumps({'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                      'execution_authorized': False, 'production_admission': False}))


if __name__ == '__main__':
    main()
