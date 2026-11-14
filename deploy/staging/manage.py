"""Explicit operator tool for a new transient preview, never existing HOPE units.

Gate records are accountable operator declarations, NOT independent admission
authority. No gate template with fabricated passing values is supplied.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import zipfile

ROOT = Path('/opt/hope-staging')
UNIT = 'hope-staging-preview.service'
SYSTEMCTL = '/usr/bin/systemctl'
RUN = '/usr/bin/systemd-run'
PYTHON = '/usr/bin/python3'
FILES = {'server.py', 'index.html', 'manage.py', 'deploy.sh', 'rollback.sh'}
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C', 'LC_ALL': 'C'}
GATES = {'restore_rehearsal', 'backup_integrity', 'capacity', 'service_baseline',
         'isolated_paths', 'nontrading_review', 'rollback_rehearsal'}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(value, length=64):
    require(type(value) is str and re.fullmatch(f'[0-9a-f]{{{length}}}', value), 'STAGING_DIGEST_INVALID')
    return value


def trusted_path(path, directory=False):
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == 0 and not info.st_mode & 0o022
            and (directory or info.st_nlink == 1), 'STAGING_PATH_UNTRUSTED')


def ancestors(path):
    for parent in reversed((path, *path.parents)):
        trusted_path(parent, directory=True)


def gates(path, commit, now=None):
    ancestors(path.parent)
    trusted_path(path)
    require(path.stat().st_size <= 32768, 'STAGING_GATE_SIZE')
    record = json.loads(path.read_bytes())
    require(type(record) is dict and set(record) == {'schema', 'commit', 'operator', 'recorded_at', 'evidence'}
            and record['schema'] == 'hope.staging-operator-gates.v1' and record['commit'] == commit
            and type(record['operator']) is str and 0 < len(record['operator']) <= 100
            and type(record['evidence']) is dict and set(record['evidence']) == GATES,
            'STAGING_GATES_INVALID')
    stamp = datetime.fromisoformat(record['recorded_at'])
    require(stamp.tzinfo is not None and 0 <= ((now or datetime.now(timezone.utc)) - stamp).total_seconds() <= 1800,
            'STAGING_GATES_EXPIRED')
    for entry in record['evidence'].values():
        require(type(entry) is dict and set(entry) == {'path', 'sha256'}, 'STAGING_GATES_INVALID')
        evidence = Path(entry['path'])
        require(evidence.is_absolute() and evidence != path, 'STAGING_EVIDENCE_INVALID')
        ancestors(evidence.parent)
        trusted_path(evidence)
        require(0 < evidence.stat().st_size <= 1048576
                and hashlib.sha256(evidence.read_bytes()).hexdigest() == sha(entry['sha256']), 'STAGING_EVIDENCE_INVALID')
    return record


def payload(archive, expected, commit):
    sha(commit, 40)
    require(len(archive) <= 1048576 and hashlib.sha256(archive).hexdigest() == sha(expected), 'STAGING_ARCHIVE_DIGEST')
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        entries = z.infolist()
        require(len(entries) == 6 and {i.filename for i in entries} == FILES | {'MANIFEST.json'}
                and all(i.file_size <= 262144 and stat.S_ISREG(i.external_attr >> 16) for i in entries),
                'STAGING_ARCHIVE_MEMBERS')
        files = {i.filename: z.read(i) for i in entries}
    manifest = json.loads(files.pop('MANIFEST.json'))
    require(type(manifest) is dict and set(manifest) == {'schema', 'commit', 'files', 'scope'}
            and manifest['schema'] == 'hope.nontrading-staging.v1' and manifest['commit'] == commit
            and manifest['scope'] == 'PREVIEW_ONLY_NO_DATABASE'
            and type(manifest['files']) is dict and set(manifest['files']) == FILES,
            'STAGING_MANIFEST_INVALID')
    require(all(hashlib.sha256(data).hexdigest() == manifest['files'][name]
                for name, data in files.items()), 'STAGING_PAYLOAD_MISMATCH')
    files['MANIFEST.json'] = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode() + b'\n'
    return files


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30, env=ENV).stdout


def state():
    result = subprocess.run([SYSTEMCTL, 'show', UNIT,
        '--property=LoadState,ActiveState,Transient,WorkingDirectory,Description'],
        check=False, capture_output=True, text=True, timeout=30, env=ENV)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    # systemctl can return nonzero for an explicitly reported nonexistent unit.
    # Do not turn arbitrary DBus/permission failures into permission to install.
    require(result.returncode == 0 or (result.returncode == 1 and values.get('LoadState') == 'not-found'),
            'STAGING_UNIT_INSPECTION_FAILED')
    return values


def install(archive, expected, commit, gate_path):
    # All denied gates and artifact checks precede mkdir/systemd mutation.
    require(os.name == 'posix' and os.geteuid() == 0, 'STAGING_ROOT_OPERATOR_REQUIRED')
    sha(commit, 40)
    ancestors(ROOT)
    gates(gate_path, commit)
    trusted_path(archive)
    require(archive.stat().st_size <= 1048576, 'STAGING_ARCHIVE_SIZE')
    files = payload(archive.read_bytes(), expected, commit)
    require(shutil.disk_usage(ROOT).free >= 2 * 1024**3, 'STAGING_DISK_LOW')
    require(os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE') >= 512 * 1024**2, 'STAGING_MEMORY_LOW')
    require(state().get('LoadState') == 'not-found', 'STAGING_UNIT_EXISTS')
    # No environment, venv, dependency installer, production paths or database.
    releases = ROOT / 'releases'
    if not releases.exists():
        releases.mkdir(mode=0o755)
    ancestors(releases)
    release = releases / commit
    release.mkdir(mode=0o700)  # Never overwrite an existing or interrupted release.
    for name, data in files.items():
        with (release / name).open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        (release / name).chmod(0o444)
    release.chmod(0o555)
    gate_path = gate_path.resolve(strict=True)
    gates(gate_path, commit)  # Expiry/evidence are rechecked immediately before launch.
    args = [RUN, '--unit=' + UNIT, '--description=HOPE_NONTRADING_' + commit,
            '--property=DynamicUser=yes', '--property=NoNewPrivileges=yes',
            '--property=ProtectSystem=strict', '--property=ProtectHome=yes',
            '--property=PrivateTmp=yes', '--property=PrivateDevices=yes',
            '--property=ProtectKernelTunables=yes', '--property=ProtectKernelModules=yes',
            '--property=ProtectControlGroups=yes', '--property=RestrictSUIDSGID=yes',
            '--property=CapabilityBoundingSet=', '--property=RestrictNamespaces=yes',
            '--property=RestrictAddressFamilies=AF_INET', '--property=IPAddressDeny=any',
            '--property=IPAddressAllow=localhost', '--property=MemoryMax=128M',
            '--property=TasksMax=16', '--property=LimitNOFILE=64',
            '--property=UnsetEnvironment=LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT PYTHONPATH PYTHONHOME PYTHONSTARTUP HOPE_DATABASE_URL HOPE_PAPER_FACTORY',
            '--property=UMask=0077', '--property=Restart=no',
            '--property=WorkingDirectory=' + str(release),
            PYTHON, '-I', '-S', '-B', str(release / 'server.py'), '--commit', commit]
    command(*args)
    # This is a launch acknowledgement, never health/DB/independent acceptance.
    return {'state': 'START_REQUESTED', 'commit': commit, 'execution_authorized': False}


def rollback(commit):
    require(os.name == 'posix' and os.geteuid() == 0, 'STAGING_ROOT_OPERATOR_REQUIRED')
    sha(commit, 40)
    release = ROOT / 'releases' / commit
    ancestors(release)
    observed = state()
    require(observed.get('Transient') == 'yes'
            and observed.get('WorkingDirectory') == str(release)
            and observed.get('Description') == 'HOPE_NONTRADING_' + commit, 'STAGING_ROLLBACK_IDENTITY')
    command(SYSTEMCTL, 'stop', UNIT)
    after = state()
    require(after.get('ActiveState') in {'inactive', 'failed'} or after.get('LoadState') == 'not-found',
            'STAGING_STOP_UNCONFIRMED')
    return {'state': 'STOPPED', 'commit': commit, 'release_retained': True, 'execution_authorized': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    deploy = commands.add_parser('install')
    deploy.add_argument('--archive', type=Path, required=True)
    deploy.add_argument('--archive-sha256', required=True)
    deploy.add_argument('--commit', required=True)
    deploy.add_argument('--gates', type=Path, required=True)
    stop = commands.add_parser('rollback')
    stop.add_argument('--commit', required=True)
    args = parser.parse_args(argv)
    if args.action == 'install':
        result = install(args.archive, args.archive_sha256, args.commit, args.gates)
    else:
        result = rollback(args.commit)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
