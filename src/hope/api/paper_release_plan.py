"""Bounded preparation artifacts only: no install, migration, activation or network."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath

from hope.api.paper_controller import plan
from hope.api.paper_preflight import migration_compatibility
from hope.infrastructure.postgres.migrations import migration_inventory

ROOT = PurePosixPath('/opt/hope-operations')


def pinned_service(sha, receipt, factory):
    """Render an inert systemd drop-in; preserve the reviewed base unit hardening."""
    plan(sha, receipt, factory)
    release = ROOT / 'releases' / sha
    bootstrap = f'/usr/bin/python3 -I -B -S -X pycache_prefix=/nonexistent/hope-runtime-bootstrap {release}/deploy/operations/paper_release.py'
    return ('# Preparation only; explicit installation/activation approval required.\n'
        '[Service]\n'
        f'WorkingDirectory={release}\n'
        f'Environment=HOPE_DEPLOYED_COMMIT={sha} HOPE_LIVE_ENABLED=false\n'
        'ExecStartPre=\n'
        f'ExecStartPre={bootstrap} --runtime-mode release --preflight\n'
        'ExecStart=\n'
        f'ExecStart={bootstrap} --runtime-mode release --factory {factory} '
        f'--deployment-receipt {receipt} --controller-directory /var/lib/hope-controller '
        f'--migrations-dir {release}/migrations --status-file /var/lib/hope-paper/health.json --poll-seconds 30\n')


def upgrade_plan(connection, directory, *, expected_schema_sha256=None):
    """Observe exact reviewed chain and prefix. Pending DDL never means GO."""
    inventory = migration_inventory(directory)
    observation = migration_compatibility(connection, directory)
    applied = observation['applied']
    prefix_valid = (applied is not None and observation['unknown'] == observation['mismatched'] == observation['unsigned'] == 0
                    and observation['code'] in {'REVIEWED_MIGRATIONS_PENDING', 'MIGRATION_RECORDS_VERIFIED'})
    from hope.infrastructure.postgres.schema_verification import schema_observation
    catalog = schema_observation(connection,expected_schema_sha256) if connection is not None else dict(state='UNVERIFIED',code='READONLY_POSTGRES_CONNECTION_REQUIRED',sha256=None,objects=None)
    return dict(catalog=catalog,schema='hope.migration-upgrade-plan.v1', state=observation['state'], code=observation['code'],
        applied=applied, required=len(inventory), prefix_verified=prefix_valid,
        pending=[dict(name=item.name, checksum=item.checksum) for item in inventory[applied:]] if prefix_valid else None,
        inventory=[dict(name=item.name, checksum=item.checksum) for item in inventory],
        database_writes=False, execution_authorized=False, restore_only_recovery=True,
        deployment_go=False, first_cycle_go=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Render inert release-pinned PAPER service and migration inventory; never installs or connects')
    parser.add_argument('--release', required=True)
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--factory', required=True)
    parser.add_argument('--migrations-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        unit = pinned_service(args.release, args.receipt, args.factory)
        migrations = upgrade_plan(None, args.migrations_dir)
        print(json.dumps(dict(schema='hope.paper-release-plan.v1', release_commit=args.release,
            source_state='UNVERIFIED', service_drop_in=unit, migrations=migrations, deployment_go=False, first_cycle_go=False,
            blockers=['CURRENT_HOST_EVIDENCE_REQUIRED','APPROVED_MIGRATION_REHEARSAL_REQUIRED',
                'INDEPENDENT_EXECUTION_AUTHORITY_REQUIRED','CURRENT_PROVIDER_DATA_CALENDAR_EVIDENCE_REQUIRED']),sort_keys=True))
        return 0
    except Exception:
        print('{"schema":"hope.paper-release-plan.v1","state":"BLOCKED","code":"RELEASE_PLAN_UNVERIFIED","deployment_go":false,"first_cycle_go":false}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
