"""Non-destructive PAPER host/release preflight; never activation authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time

from sqlalchemy import create_engine, text

from hope.api import deployment_contract as deployment
from hope.api import paper_controller as controller
from hope.api import runtime_integrity as runtime
from hope.api.operations_deployment import _activation_guard, inspect_release

LIMIT = 1000
STAGES = ('source', 'host', 'migrations', 'image', 'deployment', 'controller')


def require(condition):
    if not condition:
        raise RuntimeError('PAPER_PREFLIGHT_UNVERIFIED')


def inventory(directory):
    """Match migration runner UTF-8/universal-newline checksum semantics."""
    directory = Path(directory)
    before = runtime._trusted(directory, directory=True)
    files = sorted(directory.glob('*.sql'))
    require(0 < len(files) <= LIMIT)
    result = {}
    identities = []
    deadline = time.monotonic() + runtime.MAX_SECONDS
    total = 0
    for number, path in enumerate(files, 1):
        require(re.fullmatch(r'[0-9]{3}_[a-z0-9_]+\.sql', path.name)
                and int(path.name[:3]) == number)
        identity = runtime._trusted(path)
        payload = runtime._read(path, 1024*1024, deadline=deadline)
        total += len(payload)
        require(total <= 16*1024*1024)
        normalized = payload.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
        result[path.name] = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
        identities.append((path.name, identity))
    require(runtime._trusted(directory, directory=True) == before)
    return result, (before, identities)


def _milliseconds(value):
    match = re.fullmatch(r'([0-9]+)(ms|s|min)?', value)
    require(match is not None)
    return int(match[1]) * {None:1, 'ms':1, 's':1000, 'min':60000}[match[2]]


def migration_compatibility(connection, directory, *, schema='public'):
    """Observe records only in an existing bounded read-only PG transaction.

    No DDL, DML, migrations, timeout changes, transaction-mode changes or repair.
    This verifies migration records, not arbitrary SQL object definitions.
    """
    report = dict(state='UNVERIFIED', code='READONLY_POSTGRES_CONNECTION_REQUIRED',
                  expected=None, applied=None, pending=None, unknown=None, mismatched=None, unsigned=None)
    if connection is None:
        return report
    try:
        require(re.fullmatch('[a-z_][a-z0-9_]{0,62}', schema) is not None)
        require(connection.dialect.name == 'postgresql')
        show = lambda setting: connection.execute(text('SHOW '+setting)).scalar_one()
        require(show('transaction_read_only') == 'on'
                and int(show('server_version_num'))//10000 in {16,17}
                and show('transaction_isolation') in {'repeatable read', 'serializable'}
                and 0 < _milliseconds(show('statement_timeout')) <= 30000
                and 0 < _milliseconds(show('lock_timeout')) <= 10000)
        report['code'] = 'MIGRATION_SOURCE_UNVERIFIED'
        expected, identity = inventory(directory)
        report['expected'] = len(expected)
        report['code'] = 'MIGRATION_RECORDS_REQUIRED'
        qualified = schema+'.hope_schema_migrations'
        require(connection.execute(text('SELECT to_regclass(:table)'), {'table':qualified}).scalar_one() is not None)
        # schema passed the strict identifier grammar; no user SQL is accepted.
        query = text(f'SELECT version, checksum FROM "{schema}".hope_schema_migrations ORDER BY version LIMIT 1001')
        rows = [tuple(row) for row in connection.execute(query)]
        require(len(rows) <= LIMIT)
        applied = {}
        for version, checksum in rows:
            require(isinstance(version, str) and len(version) <= 200 and version not in applied
                    and (checksum is None or controller.is_hash(checksum)))
            applied[version] = checksum
        unknown = set(applied)-set(expected)
        pending = set(expected)-set(applied)
        unsigned = [name for name, checksum in applied.items() if checksum is None]
        mismatched = [name for name in set(applied)&set(expected)
                      if applied[name] is not None and applied[name] != expected[name]]
        report.update(applied=len(applied), pending=len(pending), unknown=len(unknown),
                      mismatched=len(mismatched), unsigned=len(unsigned))
        require(inventory(directory) == (expected, identity)
                and [tuple(row) for row in connection.execute(query)] == rows
                and show('transaction_read_only') == 'on')
        if unknown or mismatched:
            report.update(state='BLOCKED', code='MIGRATION_HISTORY_MISMATCH')
        elif pending:
            report.update(state='BLOCKED', code='REVIEWED_MIGRATIONS_PENDING')
        elif unsigned:
            report.update(code='MIGRATION_CHECKSUM_EVIDENCE_REQUIRED')
        else:
            report.update(state='VERIFIED', code='MIGRATION_RECORDS_VERIFIED')
    except Exception:
        report['state'] = 'BLOCKED'
    return report


def inspect_preflight(root, sha, *, connection=None, receipt=None, factory=None,
                      controller_directory=Path('/var/lib/hope-controller'), now=None):
    """Collect independent stages under the established shared release fence."""
    live_clock = now is None
    now = int(time.time()) if now is None else now
    report = dict(schema='hope.paper-preflight.v1', state='UNVERIFIED', release_commit=None,
                  observed_at=now, stages={name:dict(state='UNVERIFIED', code='PREREQUISITE_REQUIRED') for name in STAGES},
                  migrations=None, execution_authorized=False, host_changes=False, database_writes=False, live_enabled=False)
    stages = report['stages']
    stages['source']['code'] = 'APPROVED_RELEASE_REQUIRED'
    root = Path(root)
    try:
        runtime._sha(sha)
        report['release_commit'] = sha
        stages['source']['code'] = 'OPERATIONS_ROOT_UNVERIFIED'
        with _activation_guard(root, shared=True):
            before = runtime._trusted(root, directory=True)
            release = root/'releases'/sha
            try:
                inspect_release(release, sha)
                source = runtime.source_snapshot(release)
                stages['source'] = dict(state='VERIFIED', code='RELEASE_SOURCE_VERIFIED')
            except Exception:
                stages['source'] = dict(state='BLOCKED', code='RELEASE_SOURCE_UNVERIFIED')
                raise RuntimeError('PAPER_PREFLIGHT_SOURCE_REQUIRED') from None
            host = None
            try:
                host = deployment.host_observation(connection=connection)
                require(host['python_version'] == [3,13] and host['systemd_major'] >= 249
                        and host['postgres_major'] in {None,16,17})
                stages['host'] = dict(state='UNVERIFIED' if host['postgres_major'] is None else 'VERIFIED',
                                      code='READONLY_POSTGRES_CONNECTION_REQUIRED' if host['postgres_major'] is None else 'HOST_PREREQUISITES_VERIFIED')
            except Exception:
                stages['host'] = dict(state='BLOCKED', code='HOST_PREREQUISITES_UNVERIFIED')
            migrations = migration_compatibility(connection, release/'migrations')
            report['migrations'] = {key:value for key,value in migrations.items() if key not in {'state','code'}}
            stages['migrations'] = {key:migrations[key] for key in ('state','code')}
            folder = root/'image-evidence'
            try:
                image = deployment.verify_image(folder/'oci',folder/'approval.json',folder/'images-ed25519.pub',
                                                 folder/'policy.json',folder/'policy-ed25519.pub',expected_sha=sha,now=now)
                require(image['state'] == 'VERIFIED' and image['scope'] == 'PRODUCTION_PAPER')
                stages['image'] = dict(state='VERIFIED', code='PRODUCTION_IMAGE_VERIFIED')
            except Exception:
                stages['image'] = dict(state='BLOCKED', code='SIGNED_PRODUCTION_IMAGE_REQUIRED')
            proof = None
            try:
                pinned = controller.plan(sha,receipt,factory)
                proof = deployment.inspect_deployment(root,sha,expected_receipt=receipt,expected_factory=factory,
                                                       host_probe=lambda:host)
                controller._proof(proof,pinned,now)
                stages['deployment'] = dict(state='VERIFIED', code='DEPLOYMENT_CONTRACT_VERIFIED')
            except Exception:
                stages['deployment'] = dict(state='BLOCKED', code='PINNED_DEPLOYMENT_CONTRACT_REQUIRED')
            try:
                require(stages['deployment']['state'] == 'VERIFIED')
                observed = controller.observation(controller_directory,root,expected_plan=pinned,
                                                  inspector=lambda *a,**k:proof)
                require(observed['state'] == 'VERIFIED')
                stages['controller'] = dict(state='VERIFIED', code='CONTROLLER_VERIFICATION_CURRENT')
            except Exception:
                stages['controller'] = dict(state='BLOCKED', code='CURRENT_CONTROLLER_VERIFICATION_REQUIRED')
            # Rebind source, root and independently expiring image/deployment
            # evidence after slow DB/host probes. No cached green stage escapes.
            inspect_release(release,sha)
            require(runtime.source_snapshot(release) == source and runtime._trusted(root,directory=True) == before)
            if all(value['state'] == 'VERIFIED' for value in stages.values()):
                closing = int(time.time()) if live_clock else now
                require(now <= closing < min(image['expires_at'], proof['expires_at'], observed['expires_at']))
                closing_proof = deployment.inspect_deployment(root,sha,expected_receipt=receipt,expected_factory=factory,
                                                               host_probe=lambda:deployment.host_observation(connection=connection))
                require(closing_proof == proof)
                closing_controller = controller.observation(controller_directory,root,expected_plan=pinned,
                                                              inspector=lambda *a,**k:closing_proof)
                require(closing_controller['state'] == 'VERIFIED'
                        and closing_controller.get('sequence') == observed.get('sequence'))
                finished = int(time.time()) if live_clock else now
                require(now <= finished < min(image['expires_at'],proof['expires_at'],closing_controller['expires_at']))
                inspect_release(release,sha)
                require(runtime.source_snapshot(release) == source and runtime._trusted(root,directory=True) == before)
                report.update(state='VERIFIED',observed_at=finished)
            else:
                report['state'] = 'BLOCKED'
    except Exception:
        report['state'] = 'BLOCKED'
        if stages['source']['state'] == 'UNVERIFIED':
            stages['source']['state'] = 'BLOCKED'
        # A failed closing observation invalidates every earlier success.
        for name,value in stages.items():
            if value['state'] == 'VERIFIED':
                stages[name] = dict(state='BLOCKED',code='PREFLIGHT_WINDOW_UNVERIFIED')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only PAPER preflight; never installs, migrates or activates')
    parser.add_argument('release')
    parser.add_argument('--root',type=Path,default=Path('/opt/hope-operations'))
    parser.add_argument('--controller-directory',type=Path,default=Path('/var/lib/hope-controller'))
    parser.add_argument('--receipt')
    parser.add_argument('--factory')
    parser.add_argument('--database-readonly',action='store_true',help='Explicitly inspect the configured HOPE database in a bounded read-only transaction')
    args = parser.parse_args(argv)
    engine = None
    try:
        options = dict(receipt=args.receipt,factory=args.factory,controller_directory=args.controller_directory)
        if args.database_readonly:
            # Even explicitly requested DB discovery waits for trusted source.
            runtime._sha(args.release)
            with _activation_guard(args.root,shared=True):
                runtime._trusted(args.root,directory=True)
                inspect_release(args.root/'releases'/args.release,args.release)
                runtime.source_snapshot(args.root/'releases'/args.release)
                engine = create_engine(os.environ['HOPE_DATABASE_URL'],isolation_level='REPEATABLE READ',
                                       connect_args={'connect_timeout':3,'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
                with engine.connect() as connection:
                    report = inspect_preflight(args.root,args.release,connection=connection,**options)
        else:
            report = inspect_preflight(args.root,args.release,**options)
        print(json.dumps(report,sort_keys=True,separators=(',',':')))
        return 0 if report['state'] == 'VERIFIED' else 1
    except Exception:
        print('{"schema":"hope.paper-preflight.v1","state":"BLOCKED","code":"PREFLIGHT_UNAVAILABLE","execution_authorized":false}')
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
