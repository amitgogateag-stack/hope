"""File-only private preview transaction; no service, DB, credential or runtime activation.

Caller must first verify the asset bundle's genuine build attestation and obtain
the operator's approval of the exact plan/asset/payload hashes. These hashes are
change-window pins, not an independent production admission mechanism.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Callable
import uuid

ORDER = ('market-observations.json', 'market.html', 'index.html')
ROOT = Path('/opt/hope-dashboard-preview-20261122')
PORT = 8791
BACKUPS = Path('/opt/hope-dashboard-preview-backups')
LIMIT = 2 * 1024 * 1024


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError('PREVIEW_' + code)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def trusted_directory(path: Path, uid: int) -> None:
    s = path.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid == uid and not s.st_mode & 0o022,
            'DIRECTORY_UNTRUSTED')


def service_identity() -> dict:
    fields = 'ActiveState,SubState,MainPID,User,Group,DynamicUser,ExecStart,ExecMainStartTimestampMonotonic'
    command = ['/usr/bin/systemctl', 'show', 'hope-dashboard-preview-20261122.service',
               '--property=' + fields, '--no-pager']
    def unit():
        result = subprocess.run(command, check=True, capture_output=True, timeout=8)
        require(len(result.stdout) <= 8192, 'SERVICE_INVALID')
        return dict(line.split('=', 1) for line in result.stdout.decode().splitlines() if '=' in line)
    value = unit()
    require(set(value) == set(fields.split(',')) and value['ActiveState'] == 'active'
            and value['SubState'] == 'running' and int(value['MainPID']) > 0
            and value['User'] == 'hope-dashboard-preview-20261122'
            and value['DynamicUser'] == 'yes'
            and ('argv[]=/usr/bin/python3 -m http.server ' + str(PORT) + ' --bind 127.0.0.1 --directory '
                 + str(ROOT) + ' ;') in value['ExecStart'], 'SERVICE_INVALID')
    process = Path('/proc') / value['MainPID']
    def proc_read(name, limit):
        with (process / name).open('rb') as f:
            b = f.read(limit + 1)
        require(0 < len(b) <= limit, 'PROCESS_METADATA_INVALID')
        return b
    def start_ticks():
        return proc_read('stat', 4096).decode().rsplit(') ', 1)[1].split()[19]
    before = start_ticks()
    status = dict(line.split(':', 1) for line in proc_read('status', 16384).decode().splitlines() if ':' in line)
    uids = [int(x) for x in status['Uid'].split()]
    gids = [int(x) for x in status['Gid'].split()]
    require(len(uids) == len(gids) == 4 and len(set(uids)) == len(set(gids)) == 1
            and uids[0] > 0 and gids[0] > 0, 'READER_IDENTITY_INVALID')
    expected = ['/usr/bin/python3', '-m', 'http.server', str(PORT), '--bind', '127.0.0.1', '--directory', str(ROOT)]
    require(proc_read('cmdline', 4096) == b'\0'.join(x.encode() for x in expected) + b'\0'
            and before == start_ticks() and unit() == value, 'SERVICE_PROCESS_CHANGED')
    kernel = {'reader_uid': uids[0], 'reader_gid': gids[0], 'start_ticks': before}
    material = {'service': value, 'kernel_identity': kernel}
    return {'sha256': digest(json.dumps(material, sort_keys=True, separators=(',', ':')).encode()),
            'reader_uid': uids[0], 'reader_gid': gids[0]}


def read_file(path: Path, uid: int, limit: int = LIMIT) -> tuple[bytes, dict]:
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid == uid and not s.st_mode & 0o022
            and 0 < s.st_size <= limit, 'FILE_UNTRUSTED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        actual = os.fstat(f.fileno())
        b = f.read(limit + 1)
        closing = os.fstat(f.fileno())
    fields = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
    require(fields(s) == fields(actual) == fields(closing) and len(b) == s.st_size,
            'FILE_CHANGED')
    return b, {'sha256': digest(b), 'uid': s.st_uid, 'gid': s.st_gid,
               'mode': stat.S_IMODE(s.st_mode)}


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_file(path: Path, data: bytes, uid: int, gid: int, mode: int) -> None:
    temporary = path.with_name('.preview-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            os.fchown(f.fileno(), uid, gid)
            os.fchmod(f.fileno(), mode)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def json_object(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('PREVIEW_JSON_INVALID')))
    require(isinstance(value, dict), 'JSON_OBJECT_REQUIRED')
    return value


def observation_payload(data: bytes) -> None:
    value = json_object(data)
    require(isinstance(value, dict) and value.get('schema') == 'hope.market-observation.v1'
            and value.get('scope') == 'PRIVATE_OBSERVATION_ONLY'
            and all(value.get(k) is False for k in ('execution_authorized', 'live_enabled',
                                                   'independent_verified', 'pit_certified')),
            'PAYLOAD_SCOPE_INVALID')
    require(value.get('scanner') == {'state': 'NOT_EVALUATED',
            'reason': 'NO_QUALIFIED_STRATEGY_BINDING', 'signals': None}
            and value.get('paper') == {'state': 'NOT_EXECUTED', 'positions': None,
                                      'cash': None, 'pnl': None}, 'FINANCIAL_AUTHORITY_FORBIDDEN')


class PreviewFiles:
    """The CLI fixes production paths; temporary layouts are exercised by native tests."""

    def __init__(self, root: Path, backups: Path, uid: int,
                 observe_service: Callable[[], dict] = service_identity):
        self.root, self.backups, self.uid = root, backups, uid
        self.observe_service = observe_service

    def current(self, name: str):
        path = self.root / name
        if not path.exists() and not path.is_symlink():
            return None
        return read_file(path, self.uid)[1]

    def check(self, plan: dict) -> None:
        trusted_directory(self.root, self.uid)
        trusted_directory(self.backups, self.uid)
        require(set(plan) == {'schema', 'source_commit', 'before', 'snapshot_sha256',
                'after', 'reader_gid', 'service_identity_sha256', 'scope', 'execution_authorized'}, 'PLAN_INVALID')
        require(plan['schema'] == 'hope.private-preview-file-plan.v1'
                and plan['scope'] == 'PRIVATE_STATIC_PREVIEW_ONLY'
                and plan['execution_authorized'] is False
                and isinstance(plan['source_commit'], str)
                and re.fullmatch('[0-9a-f]{40}', plan['source_commit']) is not None
                and type(plan['reader_gid']) is int and plan['reader_gid'] > 0,
                'PLAN_INVALID')
        require(set(plan['before']) == set(ORDER) and set(plan['after']) == set(ORDER), 'PLAN_INVALID')
        require(plan['before']['index.html'] is not None, 'EXISTING_DASHBOARD_REQUIRED')
        require(plan['before']['index.html']['mode'] in {0o444, 0o644}, 'EXISTING_READ_POLICY_UNSUPPORTED')
        identity = self.observe_service()
        require(identity['sha256'] == plan['service_identity_sha256']
                and identity['reader_uid'] > 0 and identity['reader_gid'] == plan['reader_gid'],
                'SERVICE_IDENTITY_CHANGED')
        for old in plan['before'].values():
            require(old is None or (isinstance(old, dict)
                    and set(old) == {'sha256', 'uid', 'gid', 'mode'}
                    and old['uid'] == self.uid and type(old['gid']) is int and old['gid'] >= 0
                    and type(old['mode']) is int and 0 <= old['mode'] <= 0o777
                    and not old['mode'] & 0o022
                    and isinstance(old['sha256'], str)
                    and re.fullmatch('[0-9a-f]{64}', old['sha256']) is not None), 'PLAN_INVALID')
        for value in plan['after'].values():
            require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None,
                    'PLAN_INVALID')
        require(digest(read_file(self.root / 'snapshot.json', self.uid, 32768)[0])
                == plan['snapshot_sha256'], 'SNAPSHOT_CHANGED')

    def apply(self, plan: dict, payloads: dict[str, bytes],
              after_write: Callable[[str], None] = lambda _: None) -> Path:
        self.check(plan)
        require(set(payloads) == set(ORDER), 'FILE_SET_INVALID')
        for name in ORDER:
            require(self.current(name) == plan['before'][name], 'BEFORE_STATE_CHANGED')
            require(isinstance(payloads[name], bytes) and 0 < len(payloads[name])
                    <= (LIMIT if name.endswith('.json') else 65536)
                    and digest(payloads[name]) == plan['after'][name], 'PAYLOAD_CHANGED')
        observation_payload(payloads['market-observations.json'])
        backup = self.backups / ('market-' + plan['source_commit'])
        require(not backup.exists() and not backup.is_symlink(), 'BACKUP_EXISTS_NO_REPLAY')
        backup.mkdir(mode=0o700)
        sync_directory(self.backups)
        for name in ORDER:
            if plan['before'][name] is not None:
                b, metadata = read_file(self.root / name, self.uid)
                require(metadata == plan['before'][name], 'BEFORE_STATE_CHANGED')
                atomic_file(backup / name, b, self.uid, os.getegid(), 0o600)
        record = {'schema': 'hope.private-preview-file-journal.v1', 'phase': 'PREPARED', 'plan': plan}
        self.journal(backup, record)
        for name in ORDER:
            require(self.current(name) == plan['before'][name], 'STATE_CHANGED_DURING_APPLY')
            policy = plan['before']['index.html']
            atomic_file(self.root / name, payloads[name], self.uid, policy['gid'], policy['mode'])
            after_write(name)
        self.check(plan)
        require(all(self.current(name) == self.installed(plan, name) for name in ORDER),
                'CLOSING_STATE_CHANGED')
        record['phase'] = 'COMMITTED'
        self.journal(backup, record)
        return backup

    def installed(self, plan: dict, name: str) -> dict:
        policy = plan['before']['index.html']
        return {'sha256': plan['after'][name], 'uid': self.uid,
                'gid': policy['gid'], 'mode': policy['mode']}

    def journal(self, backup: Path, record: dict) -> None:
        atomic_file(backup / 'JOURNAL.json', json.dumps(record, sort_keys=True).encode(),
                    self.uid, os.getegid(), 0o600)

    def rollback(self, backup: Path, approved_plan: dict,
                 after_write: Callable[[str], None] = lambda _: None) -> None:
        require(backup.parent == self.backups, 'BACKUP_PATH_INVALID')
        trusted_directory(backup, self.uid)
        record = json_object(read_file(backup / 'JOURNAL.json', self.uid, 16384)[0])
        require(set(record) == {'schema', 'phase', 'plan'}
                and record['schema'] == 'hope.private-preview-file-journal.v1'
                and record['phase'] in {'PREPARED', 'COMMITTED', 'ROLLING_BACK'}, 'JOURNAL_INVALID')
        plan = record['plan']
        require(plan == approved_plan, 'ROLLBACK_APPROVAL_MISMATCH')
        self.check(plan)
        require(backup.name == 'market-' + plan['source_commit'], 'BACKUP_PATH_INVALID')
        originals = {}
        for name in ORDER:
            old, current = plan['before'][name], self.current(name)
            installed = current == self.installed(plan, name)
            require(current == old or installed, 'ROLLBACK_EXTERNAL_DRIFT')
            if old is not None:
                b, _ = read_file(backup / name, self.uid)
                require(digest(b) == old['sha256'], 'BACKUP_CHANGED')
                originals[name] = b
        record['phase'] = 'ROLLING_BACK'
        self.journal(backup, record)
        for name in reversed(ORDER):
            old = plan['before'][name]
            if self.current(name) == old:
                continue
            if old is None:
                (self.root / name).unlink()
                sync_directory(self.root)
            else:
                atomic_file(self.root / name, originals[name], old['uid'], old['gid'], old['mode'])
            after_write(name)
        record['phase'] = 'ROLLED_BACK'
        self.check(plan)
        require(all(self.current(name) == plan['before'][name] for name in ORDER), 'ROLLBACK_INCOMPLETE')
        self.journal(backup, record)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('apply', 'rollback'))
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--approved-plan-sha256', required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0, 'OWNER_REQUIRED')
    require(re.fullmatch('[0-9a-f]{64}', args.approved_plan_sha256) is not None, 'PLAN_PIN_INVALID')
    for path in (ROOT, BACKUPS, args.package):
        for parent in (path, *path.parents):
            trusted_directory(parent, 0)
    raw, _ = read_file(args.package / 'PLAN.json', 0, 16384)
    require(digest(raw) == args.approved_plan_sha256, 'APPROVED_PLAN_MISMATCH')
    plan = json_object(raw)
    layout = PreviewFiles(ROOT, BACKUPS, 0)
    # Serializes only this file transaction; it never claims a publisher/DB lock.
    import fcntl
    fd = os.open(BACKUPS / '.private-market-files.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
                'LOCK_UNTRUSTED')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.mode == 'apply':
            payloads = {name: read_file(args.package / name, 0)[0] for name in ORDER}
            layout.apply(plan, payloads)
        else:
            layout.rollback(BACKUPS / ('market-' + plan['source_commit']), plan)
    finally:
        os.close(fd)
    print(json.dumps({'state': 'FILES_APPLIED' if args.mode == 'apply' else 'FILES_ROLLED_BACK',
                      'source_commit': plan['source_commit'], 'execution_authorized': False,
                      'database_access': False, 'services_changed': False}))


if __name__ == '__main__':
    main()
