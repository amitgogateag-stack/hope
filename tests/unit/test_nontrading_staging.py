import hashlib
from datetime import datetime, timezone, timedelta
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
import zipfile

import pytest

from hope.api.staging_release import build, FILES


ROOT = Path(__file__).parents[2]
SHA = 'a' * 40


def load(name):
    spec = importlib.util.spec_from_file_location('staging_' + name, ROOT / 'deploy/staging' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Operator fixtures model the POSIX admission check via os.name. Keep the
    # real native Path class instead of allowing that global flag to select an
    # unsupported PosixPath on Windows. All actual file/digest checks remain.
    if os.name == 'nt' and hasattr(module, 'Path'):
        module.Path = type(ROOT)
    return module


def archive(tmp_path, *, change=None):
    content = {name: (ROOT / 'deploy/staging' / name).read_bytes() for name in FILES}
    manifest = dict(schema='hope.nontrading-staging.v1', commit=SHA, scope='PREVIEW_ONLY_NO_DATABASE',
                    files={k: hashlib.sha256(v).hexdigest() for k, v in content.items()})
    content['MANIFEST.json'] = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode() + b'\n'
    if change:
        change(content)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name, data in content.items():
            item = zipfile.ZipInfo(name)
            item.create_system = 3
            item.external_attr = 0o100444 << 16
            z.writestr(item, data)
    data = out.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def unpack(tmp_path):
    raw, digest = archive(tmp_path)
    files = load('manage').payload(raw, digest, SHA)
    target = tmp_path / 'runtime'
    target.mkdir()
    for name, data in files.items():
        (target / name).write_bytes(data)
    return target


def test_exact_artifact_and_runtime_manifest(tmp_path):
    target = unpack(tmp_path)
    result = load('server').verify(target, SHA)
    assert set(result['files']) == set(FILES)
    assert not any('hope' == p.name or p.suffix in {'.env', '.sql'} for p in target.iterdir())


def test_reproducible_builder_uses_committed_bytes_not_worktree(tmp_path):
    repo = tmp_path / 'repo'; repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True).stdout.decode().strip()
    git('init', '-q')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'user.name', 'Disposable test')
    for name in FILES:
        p = repo / 'deploy/staging' / name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((ROOT / 'deploy/staging' / name).read_bytes())
    git('add', '.')
    git('commit', '-qm', 'disposable')
    commit = git('rev-parse', 'HEAD')
    first, second = tmp_path / 'first.zip', tmp_path / 'second.zip'
    info = build(repo, commit, first)
    (repo / 'deploy/staging/server.py').write_text('must not package mutable bytes')
    build(repo, commit, second)
    assert first.read_bytes() == second.read_bytes()
    assert info['sha256'] == hashlib.sha256(first.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        build(repo, commit, first)
    committed_server = subprocess.check_output(['git', '-C', str(repo), 'show', commit + ':deploy/staging/server.py'])
    assert load('manage').payload(first.read_bytes(), info['sha256'], commit)['server.py'] == committed_server


def test_builder_reports_digest_from_exclusive_output_descriptor(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'; repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True).stdout.decode().strip()
    git('init', '-q')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'user.name', 'Disposable test')
    for name in FILES:
        p = repo / 'deploy/staging' / name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((ROOT / 'deploy/staging' / name).read_bytes())
    git('add', '.')
    git('commit', '-qm', 'disposable')
    commit = git('rev-parse', 'HEAD')
    destination = tmp_path / 'release.zip'
    original_read, original_stat = Path.read_bytes, Path.stat
    def no_reopen_read(path):
        if path == destination:
            pytest.fail('builder reopened completed archive by pathname')
        return original_read(path)
    def no_reopen_stat(path, *args, **kwargs):
        if path == destination:
            pytest.fail('builder restatted completed archive by pathname')
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_bytes', no_reopen_read)
    monkeypatch.setattr(Path, 'stat', no_reopen_stat)
    info = build(repo, commit, destination)
    with destination.open('rb') as stream:
        artifact = stream.read()
    assert info['sha256'] == hashlib.sha256(artifact).hexdigest()
    assert info['bytes'] == len(artifact)


@pytest.mark.parametrize('commit', ['HEAD', 'a'*39, 'A'*40, '../release', '--help'])
def test_builder_requires_exact_immutable_commit(tmp_path, commit):
    with pytest.raises(ValueError, match='STAGING_COMMIT_INVALID'):
        build(ROOT, commit, tmp_path / 'bad.zip')
    assert not (tmp_path / 'bad.zip').exists()


def gate_record(tmp_path):
    now = datetime.now(timezone.utc)
    evidence = tmp_path / 'real-test-evidence.txt'; evidence.write_text('Disposable fixture only; not host acceptance')
    record = dict(schema='hope.staging-operator-gates.v1', commit=SHA, operator='disposable-test',
        recorded_at=now.isoformat(), evidence={key: dict(path=str(evidence),
        sha256=hashlib.sha256(evidence.read_bytes()).hexdigest()) for key in load('manage').GATES})
    return now, record, evidence


@pytest.mark.parametrize('fault', [None, 'expired', 'future', 'wrong_commit', 'missing_restore', 'mutated_proof', 'extra'])
def test_operator_evidence_freshness_and_exact_digest(tmp_path, monkeypatch, fault):
    m = load('manage')
    # Model only the native root-owner/parent boundary; real fixture files,
    # schema, time window and digest checks still execute. This is not evidence
    # that the EC2 restore or operator declarations exist.
    monkeypatch.setattr(m, 'ancestors', lambda p: None)
    monkeypatch.setattr(m, 'trusted_path', lambda p: None)
    now, record, evidence = gate_record(tmp_path)
    if fault in {'expired', 'future'}:
        record['recorded_at'] = (now + timedelta(seconds=1 if fault == 'future' else -1801)).isoformat()
    if fault == 'wrong_commit': record['commit'] = 'b'*40
    if fault == 'missing_restore': del record['evidence']['restore_rehearsal']
    if fault == 'mutated_proof': evidence.write_text('replaced')
    if fault == 'extra': record['paper_enabled'] = True
    path = tmp_path / 'gates.json'; path.write_text(json.dumps(record))
    if fault:
        with pytest.raises(ValueError, match='STAGING_'):
            m.gates(path, SHA, now=now)
    else:
        assert m.gates(path, SHA, now=now)['commit'] == SHA


def test_installer_launch_isolated_exact_command_and_no_existing_units(tmp_path, monkeypatch):
    m = load('manage'); calls = []
    raw, digest = archive(tmp_path)
    source = tmp_path / 'release.zip'; source.write_bytes(raw)
    monkeypatch.setattr(m.os, 'name', 'posix')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(m.os, 'sysconf', lambda key: 1024**3 if key == 'SC_AVPHYS_PAGES' else 4096, raising=False)
    monkeypatch.setattr(m, 'ancestors', lambda p: None)
    monkeypatch.setattr(m, 'trusted_path', lambda p: None)
    gate_calls = []
    monkeypatch.setattr(m, 'gates', lambda *a: gate_calls.append(a))
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    active = dict(m.expected_identity(SHA), LoadState='loaded', ActiveState='active')
    states = iter([dict(LoadState='not-found'), active, active])
    monkeypatch.setattr(m, 'state', lambda: next(states))
    status = dict(schema='hope.nontrading-preview.v1', release_commit=SHA,
        scope='PREVIEW_ONLY_NO_DATABASE', paper_enabled=False, live_enabled=False,
        execution_authorized=False, database='NOT_CONNECTED', broker='NOT_INCLUDED',
        independent_acceptance='UNVERIFIED', operational_readiness='BLOCKED')
    monkeypatch.setattr(m, 'preview_status', lambda commit: m.validate_preview_status(status, commit))
    monkeypatch.setattr(m, 'command', lambda *a: calls.append(a))
    # The file must exist for the native canonical path check; gate semantics
    # have their own unmocked schema/digest tests above.
    gate = tmp_path / 'operator-gates.json'; gate.write_text('test-only modeled declaration')
    result = m.install(source, digest, SHA, gate)
    assert result['state'] == 'PREVIEW_RESPONSIVE' and result['execution_authorized'] is False
    assert result['status_sha256'] == hashlib.sha256(json.dumps(status, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert len(gate_calls) == 2 and len(calls) == 1
    argv = calls[0]
    assert argv[-7:] == (m.PYTHON, '-I', '-S', '-B', str(tmp_path / 'releases' / SHA / 'server.py'), '--commit', SHA)
    assert '--unit=hope-staging-preview.service' in argv
    assert '--property=DynamicUser=yes' in argv and '--property=ProtectSystem=strict' in argv
    assert not any('hope-paper.service' in v or '8787' in v or '8788' in v or 'EnvironmentFile' in v for v in argv)
    for p in (tmp_path / 'releases' / SHA).iterdir():
        p.chmod(0o600)  # Windows temporary-directory cleanup of read-only files.


@pytest.mark.parametrize('fault', ['extra', 'commit', 'paper', 'live', 'authorized', 'database', 'broker', 'acceptance', 'readiness', 'bool_integer'])
def test_preview_status_cannot_claim_trading_readiness_or_wrong_identity(fault):
    m = load('manage')
    value = dict(schema='hope.nontrading-preview.v1', release_commit=SHA,
        scope='PREVIEW_ONLY_NO_DATABASE', paper_enabled=False, live_enabled=False,
        execution_authorized=False, database='NOT_CONNECTED', broker='NOT_INCLUDED',
        independent_acceptance='UNVERIFIED', operational_readiness='BLOCKED')
    if fault == 'extra': value['paper_cycle'] = 'COMPLETED'
    elif fault == 'commit': value['release_commit'] = 'b' * 40
    elif fault == 'paper': value['paper_enabled'] = True
    elif fault == 'live': value['live_enabled'] = True
    elif fault == 'authorized': value['execution_authorized'] = True
    elif fault == 'database': value['database'] = 'CONNECTED'
    elif fault == 'broker': value['broker'] = 'SIMULATED'
    elif fault == 'acceptance': value['independent_acceptance'] = 'VERIFIED'
    elif fault == 'readiness': value['operational_readiness'] = 'READY'
    else: value['paper_enabled'] = 0
    with pytest.raises(ValueError, match='STAGING_PREVIEW_STATUS_INVALID'):
        m.validate_preview_status(value, SHA)


def test_failed_preview_probe_stops_unit_and_confirms_stop(tmp_path, monkeypatch):
    m = load('manage'); calls = []
    raw, digest = archive(tmp_path)
    source = tmp_path / 'release.zip'; source.write_bytes(raw)
    monkeypatch.setattr(m.os, 'name', 'posix')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(m.os, 'sysconf', lambda key: 1024**3 if key == 'SC_AVPHYS_PAGES' else 4096, raising=False)
    monkeypatch.setattr(m, 'ancestors', lambda p: None)
    monkeypatch.setattr(m, 'trusted_path', lambda p: None)
    monkeypatch.setattr(m, 'gates', lambda *a: None)
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    active = dict(m.expected_identity(SHA), LoadState='loaded', ActiveState='active')
    states = iter([dict(LoadState='not-found'), active, dict(LoadState='not-found')])
    monkeypatch.setattr(m, 'state', lambda: next(states))
    monkeypatch.setattr(m, 'preview_status', lambda commit: (_ for _ in ()).throw(ValueError('STAGING_PREVIEW_STATUS_INVALID')))
    monkeypatch.setattr(m, 'command', lambda *a: calls.append(a))
    gate = tmp_path / 'operator-gates.json'; gate.write_text('test-only modeled declaration')
    with pytest.raises(ValueError, match='STAGING_PREVIEW_STATUS_INVALID'):
        m.install(source, digest, SHA, gate)
    assert calls[-1] == (m.SYSTEMCTL, 'stop', m.UNIT)
    for p in (tmp_path / 'releases' / SHA).iterdir():
        p.chmod(0o600)


def test_installer_bytes_must_match_reviewed_archive(tmp_path, monkeypatch):
    altered = tmp_path / 'manage.py'
    altered.write_bytes((ROOT / 'deploy/staging/manage.py').read_bytes() + b'\n# altered operator tool\n')
    spec = importlib.util.spec_from_file_location('altered_staging_manage', altered)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    if os.name == 'nt':
        m.Path = type(ROOT)
    raw, digest = archive(tmp_path)
    source = tmp_path / 'release.zip'; source.write_bytes(raw)
    monkeypatch.setattr(m.os, 'name', 'posix')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(m.os, 'sysconf', lambda key: 1024**3 if key == 'SC_AVPHYS_PAGES' else 4096, raising=False)
    monkeypatch.setattr(m, 'ancestors', lambda p: None)
    monkeypatch.setattr(m, 'trusted_path', lambda p: None)
    monkeypatch.setattr(m, 'gates', lambda *a: None)
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    states = iter([dict(LoadState='not-found'), m.expected_identity(SHA)])
    monkeypatch.setattr(m, 'state', lambda: next(states))
    calls = []
    monkeypatch.setattr(m, 'command', lambda *a: calls.append(a))
    gate = tmp_path / 'operator-gates.json'; gate.write_text('test-only modeled declaration')
    with pytest.raises(ValueError, match='STAGING_INSTALLER_MISMATCH'):
        m.install(source, digest, SHA, gate)
    assert calls == [] and not (tmp_path / 'releases').exists()


@pytest.mark.parametrize('changed', list(load('manage').SANDBOX))
def test_installer_stops_unit_when_effective_sandbox_is_weaker(tmp_path, monkeypatch, changed):
    m = load('manage'); calls = []
    raw, digest = archive(tmp_path)
    source = tmp_path / 'release.zip'; source.write_bytes(raw)
    monkeypatch.setattr(m.os, 'name', 'posix')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(m.os, 'sysconf', lambda key: 1024**3 if key == 'SC_AVPHYS_PAGES' else 4096, raising=False)
    monkeypatch.setattr(m, 'ancestors', lambda p: None)
    monkeypatch.setattr(m, 'trusted_path', lambda p: None)
    monkeypatch.setattr(m, 'gates', lambda *a: None)
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    observed = dict(m.expected_identity(SHA), LoadState='loaded', ActiveState='active')
    observed[changed] = 'wrong'
    states = iter([dict(LoadState='not-found'), observed,
                   dict(LoadState='not-found')])
    monkeypatch.setattr(m, 'state', lambda: next(states))
    monkeypatch.setattr(m, 'command', lambda *a: calls.append(a))
    gate = tmp_path / 'operator-gates.json'; gate.write_text('test-only modeled declaration')
    with pytest.raises(ValueError, match='STAGING_LAUNCH_IDENTITY'):
        m.install(source, digest, SHA, gate)
    assert calls[-1] == (m.SYSTEMCTL, 'stop', m.UNIT)
    for p in (tmp_path / 'releases' / SHA).iterdir():
        p.chmod(0o600)


def test_installer_mismatch_requires_confirmed_stop(tmp_path, monkeypatch):
    m = load('manage'); calls = []
    raw, digest = archive(tmp_path)
    source = tmp_path / 'release.zip'; source.write_bytes(raw)
    monkeypatch.setattr(m.os, 'name', 'posix')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(m.os, 'sysconf', lambda key: 1024**3 if key == 'SC_AVPHYS_PAGES' else 4096, raising=False)
    monkeypatch.setattr(m, 'ancestors', lambda p: None)
    monkeypatch.setattr(m, 'trusted_path', lambda p: None)
    monkeypatch.setattr(m, 'gates', lambda *a: None)
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    mismatch = dict(m.expected_identity(SHA), ActiveState='active')
    mismatch['DynamicUser'] = 'no'
    states = iter([dict(LoadState='not-found'), mismatch,
                   dict(mismatch, ActiveState='active')])
    monkeypatch.setattr(m, 'state', lambda: next(states))
    monkeypatch.setattr(m, 'command', lambda *a: calls.append(a))
    gate = tmp_path / 'operator-gates.json'; gate.write_text('test-only modeled declaration')
    with pytest.raises(ValueError, match='STAGING_STOP_UNCONFIRMED'):
        m.install(source, digest, SHA, gate)
    assert calls[-1] == (m.SYSTEMCTL, 'stop', m.UNIT)
    for p in (tmp_path / 'releases' / SHA).iterdir():
        p.chmod(0o600)


@pytest.mark.parametrize('observed', [
    dict(LoadState='loaded', ActiveState='failed'),
    dict(LoadState='not-found', ActiveState='inactive'),
])
def test_installer_refuses_launch_acknowledgement_without_active_loaded_unit(tmp_path, monkeypatch, observed):
    m = load('manage'); calls = []
    raw, digest = archive(tmp_path)
    source = tmp_path / 'release.zip'; source.write_bytes(raw)
    monkeypatch.setattr(m.os, 'name', 'posix')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(m.os, 'sysconf', lambda key: 1024**3 if key == 'SC_AVPHYS_PAGES' else 4096, raising=False)
    monkeypatch.setattr(m, 'ancestors', lambda p: None)
    monkeypatch.setattr(m, 'trusted_path', lambda p: None)
    monkeypatch.setattr(m, 'gates', lambda *a: None)
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    states = iter([dict(LoadState='not-found'),
                   dict(m.expected_identity(SHA), **observed),
                   dict(LoadState='not-found')])
    monkeypatch.setattr(m, 'state', lambda: next(states))
    monkeypatch.setattr(m, 'command', lambda *a: calls.append(a))
    gate = tmp_path / 'operator-gates.json'; gate.write_text('test-only modeled declaration')
    with pytest.raises(ValueError, match='STAGING_LAUNCH_IDENTITY'):
        m.install(source, digest, SHA, gate)
    assert calls[-1] == (m.SYSTEMCTL, 'stop', m.UNIT)
    for p in (tmp_path / 'releases' / SHA).iterdir():
        p.chmod(0o600)


@pytest.mark.parametrize('missing', list(load('manage').SANDBOX))
def test_unit_identity_requires_every_effective_sandbox_property(missing):
    m = load('manage')
    observed = m.expected_identity(SHA)
    observed.pop(missing)
    assert m.identity_matches(observed, SHA) is False


@pytest.mark.parametrize('name', ['../server.py', '/etc/passwd', 'hope/api/paper_service.py',
    'broker.py', '.env', 'snapshot.json', 'sitecustomize.py', 'migrations/001.sql'])
def test_extra_execution_inputs_cannot_enter_archive(tmp_path, name):
    raw, digest = archive(tmp_path, change=lambda c: c.update({name: b'hostile'}))
    with pytest.raises(ValueError, match='STAGING_ARCHIVE_MEMBERS'):
        load('manage').payload(raw, digest, SHA)


@pytest.mark.parametrize('fault', ['digest', 'commit', 'tamper', 'missing', 'scope'])
def test_artifact_faults_are_denied(tmp_path, fault):
    def change(c):
        if fault == 'tamper':
            c['server.py'] += b'\nraise RuntimeError()'
        if fault == 'missing':
            del c['deploy.sh']
        if fault == 'scope':
            m = json.loads(c['MANIFEST.json']); m['scope'] = 'PAPER'; c['MANIFEST.json'] = json.dumps(m).encode()
    raw, digest = archive(tmp_path, change=change)
    with pytest.raises(ValueError, match='STAGING_'):
        load('manage').payload(raw, '0' * 64 if fault == 'digest' else digest,
                               'b' * 40 if fault == 'commit' else SHA)


def noncanonical_manifest(content, variant):
    raw = content['MANIFEST.json']
    if variant == 'whitespace':
        content['MANIFEST.json'] = raw + b' '
    else:
        content['MANIFEST.json'] = raw[:-2] + b',"scope":"PREVIEW_ONLY_NO_DATABASE"}\n'


@pytest.mark.parametrize('variant', ['whitespace', 'duplicate_scope'])
def test_installer_rejects_noncanonical_manifest_bytes(tmp_path, variant):
    raw, digest = archive(tmp_path, change=lambda content: noncanonical_manifest(content, variant))
    with pytest.raises(ValueError, match='STAGING_MANIFEST_INVALID'):
        load('manage').payload(raw, digest, SHA)


@pytest.mark.parametrize('variant', ['whitespace', 'duplicate_scope'])
def test_runtime_rejects_noncanonical_manifest_bytes(tmp_path, variant):
    target = unpack(tmp_path)
    manifest = target / 'MANIFEST.json'
    content = {'MANIFEST.json': manifest.read_bytes()}
    noncanonical_manifest(content, variant)
    manifest.write_bytes(content['MANIFEST.json'])
    with pytest.raises(ValueError, match='STAGING_MANIFEST_INVALID'):
        load('server').verify(target, SHA)


def test_duplicate_archive_entry_denied(tmp_path):
    raw, _ = archive(tmp_path)
    buf = io.BytesIO(raw)
    with pytest.warns(UserWarning), zipfile.ZipFile(buf, 'a') as z:
        z.writestr('server.py', b'bad')
    raw = buf.getvalue()
    with pytest.raises(ValueError, match='STAGING_ARCHIVE_MEMBERS'):
        load('manage').payload(raw, hashlib.sha256(raw).hexdigest(), SHA)


def test_unmanifested_runtime_input_denied(tmp_path):
    target = unpack(tmp_path)
    (target / '.env').write_text('HOPE_LIVE_ENABLED=true')
    with pytest.raises(ValueError, match='STAGING_MANIFEST_INVALID'):
        load('server').verify(target, SHA)


def test_runtime_rejects_oversized_manifest_before_parsing(tmp_path):
    target = unpack(tmp_path)
    manifest = target / 'MANIFEST.json'
    manifest.write_bytes(manifest.read_bytes() + b' ' * 262144)
    with pytest.raises(ValueError, match='STAGING_MANIFEST_INVALID'):
        load('server').verify(target, SHA)


@pytest.mark.skipif(os.name == 'nt', reason='Manifest symlink boundary is exercised on Linux CI')
def test_runtime_rejects_manifest_symlink(tmp_path):
    target = unpack(tmp_path)
    manifest = target / 'MANIFEST.json'
    external = tmp_path / 'external-manifest.json'
    external.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(external)
    with pytest.raises(ValueError, match='STAGING_MANIFEST_INVALID'):
        load('server').verify(target, SHA)


@pytest.mark.parametrize('flag', ['--factory', '--database-url', '--live', '--paper', '--port', '--snapshot'])
def test_runtime_has_no_execution_configuration(tmp_path, flag):
    target = unpack(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(target / 'server.py'),
                             '--commit', SHA, flag, 'hostile'], capture_output=True, timeout=10)
    assert result.returncode == 2
    assert b'unrecognized arguments' in result.stderr


def test_hostile_environment_does_not_import_factory_or_sitecustomize(tmp_path):
    target = unpack(tmp_path)
    hostile = tmp_path / 'hostile'; hostile.mkdir()
    marker = tmp_path / 'executed'
    for name in ['sitecustomize.py', 'factory.py']:
        (hostile / name).write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("bad")')
    env = dict(os.environ, PYTHONPATH=str(hostile), HOPE_PAPER_FACTORY='factory:run',
               HOPE_DATABASE_URL='postgresql://must-not-connect', HOPE_LIVE_ENABLED='true', HOPE_PAPER_ENABLED='true')
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(target / 'server.py'),
                             '--commit', SHA, '--check'], env=env, capture_output=True, timeout=10, check=True)
    assert json.loads(result.stdout)['execution_authorized'] is False
    assert not marker.exists()


@pytest.mark.parametrize('operation', [
    "import socket; socket.create_connection(('127.0.0.1', 5432), timeout=1)",
    "import subprocess; subprocess.run(['never-launch-a-worker'])",
    "import os; os.system('never-launch-a-worker')",
    "import sqlite3; sqlite3.connect(':memory:')",
    "open('must-not-write', 'w')",
])
def test_runtime_seal_denies_connections_workers_and_writes(tmp_path, operation):
    server = ROOT / 'deploy/staging/server.py'
    code = f"import runpy; m=runpy.run_path({str(server)!r}); m['seal_runtime'](); {operation}"
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code],
                            cwd=tmp_path, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert b'STAGING_' in result.stderr
    assert not (tmp_path / 'must-not-write').exists()


def test_missing_operator_gate_precedes_any_mutation(tmp_path, monkeypatch):
    m = load('manage'); calls = []
    monkeypatch.setattr(m.os, 'name', 'posix')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(m, 'ancestors', lambda p: None)
    monkeypatch.setattr(m, 'command', lambda *a: calls.append(a))
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    with pytest.raises(FileNotFoundError):
        m.install(tmp_path / 'missing.zip', 'a' * 64, SHA, tmp_path / 'missing-gates.json')
    assert not calls and list(tmp_path.iterdir()) == []


def test_installer_refuses_archive_from_untrusted_parent_before_read(tmp_path, monkeypatch):
    m = load('manage')
    incoming = tmp_path / 'incoming'; incoming.mkdir(mode=0o777)
    archive_path = incoming / 'release.zip'; archive_path.write_bytes(b'not-read')
    staging = tmp_path / 'staging'; staging.mkdir()
    monkeypatch.setattr(m.os, 'name', 'posix')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(m, 'ROOT', staging)
    monkeypatch.setattr(m, 'gates', lambda *a: None)
    monkeypatch.setattr(m, 'trusted_path', lambda p: None)
    def check_ancestors(path):
        if path == incoming:
            raise ValueError('STAGING_PATH_UNTRUSTED')
    monkeypatch.setattr(m, 'ancestors', check_ancestors)
    monkeypatch.setattr(m, 'payload', lambda *a: pytest.fail('untrusted archive was read'))
    with pytest.raises(ValueError, match='STAGING_PATH_UNTRUSTED'):
        m.install(archive_path, 'a' * 64, SHA, tmp_path / 'gates.json')


@pytest.mark.parametrize(('code', 'output', 'allowed'), [
    (0, 'LoadState=loaded\n', True), (1, 'LoadState=not-found\n', True),
    (1, '', False), (1, 'LoadState=loaded\n', False), (4, 'LoadState=not-found\n', False)])
def test_unit_absence_is_not_confused_with_inspection_failure(monkeypatch, code, output, allowed):
    m = load('manage')
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=code, stdout=output))
    if allowed:
        assert m.state()['LoadState'] in {'loaded', 'not-found'}
    else:
        with pytest.raises(ValueError, match='STAGING_UNIT_INSPECTION_FAILED'):
            m.state()


@pytest.mark.parametrize('changed', ['Transient', 'WorkingDirectory', 'Description', *load('manage').SANDBOX])
def test_rollback_never_stops_unmatched_unit(tmp_path, monkeypatch, changed):
    m = load('manage'); calls = []
    monkeypatch.setattr(m.os, 'name', 'posix')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(m, 'ancestors', lambda p: None)
    expected = m.expected_identity(SHA)
    expected[changed] = 'wrong'
    monkeypatch.setattr(m, 'state', lambda: expected)
    monkeypatch.setattr(m, 'command', lambda *a: calls.append(a))
    with pytest.raises(ValueError, match='STAGING_ROLLBACK_IDENTITY'):
        m.rollback(SHA)
    assert calls == []


def test_rollback_only_stops_preview_and_retains_release(tmp_path, monkeypatch):
    m = load('manage'); calls = []
    monkeypatch.setattr(m.os, 'name', 'posix')
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(m, 'ancestors', lambda p: None)
    states = iter([m.expected_identity(SHA),
                   dict(LoadState='not-found')])
    monkeypatch.setattr(m, 'state', lambda: next(states))
    monkeypatch.setattr(m, 'command', lambda *a: calls.append(a))
    assert m.rollback(SHA)['release_retained'] is True
    assert calls == [(m.SYSTEMCTL, 'stop', 'hope-staging-preview.service')]


@pytest.mark.skipif(os.name != 'posix', reason='Native loopback lifecycle is exercised on Linux CI')
def test_real_preview_http_is_nontrading_and_has_no_file_server(tmp_path):
    target = unpack(tmp_path)
    process = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(target / 'server.py'), '--commit', SHA],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        address = 'http://127.0.0.1:8790'
        deadline = time.monotonic() + 8
        while True:
            try:
                response = opener.open(address + '/status.json', timeout=1)
                break
            except urllib.error.URLError:
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail('Preview did not start')
                time.sleep(0.05)
        with response:
            observed = json.load(response)
        assert load('manage').preview_status(SHA) == observed
        assert observed['paper_enabled'] is observed['live_enabled'] is observed['execution_authorized'] is False
        assert observed['database'] == 'NOT_CONNECTED' and observed['broker'] == 'NOT_INCLUDED'
        assert observed['independent_acceptance'] == 'UNVERIFIED'
        for route, status in [('/../MANIFEST.json', 404), ('/MANIFEST.json', 404), ('/paper', 404)]:
            with pytest.raises(urllib.error.HTTPError) as exc:
                opener.open(address + route, timeout=1)
            assert exc.value.code == status
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(urllib.request.Request(address + '/', data=b'execute'), timeout=1)
        assert exc.value.code == 405
    finally:
        process.terminate()
        process.communicate(timeout=5)
    assert not any(p.name == '__pycache__' for p in target.iterdir())
