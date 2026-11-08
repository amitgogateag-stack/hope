"""Explicit signed startup route; ordinary CURRENT admission remains unchanged."""
from contextlib import contextmanager
from datetime import datetime,UTC
import json
import os
from pathlib import Path
import time

from sqlalchemy import text
from hope.api import startup_authority as authority, first_cycle_authorization as handoff
from hope.api import paper_controller as controller
from hope.api.operations_deployment import _activation_guard,verify_database_role
from hope.api.paper_preflight import migration_compatibility
from hope.infrastructure.postgres.schema_verification import schema_observation
from hope.infrastructure.repositories.paper_handoff import database_identity
from hope.infrastructure.repositories.paper_startup import SqlAlchemyPaperStartupRepository,assert_public_schema
from hope.infrastructure.paper_history import publisher_lease
from hope.infrastructure.paper_service import publish_service_status


def bootstrap(engine,permit,directory,*,role_probe=verify_database_role):
    handoff.require(type(permit) is authority.Permit and permit.kind=='startup')
    permit.assert_current()
    with engine.connect() as connection:
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        role_probe(connection,component='paper');assert_public_schema(connection)
        handoff.require(database_identity(connection)==permit.handoff.token['database_sha256'])
    # Commit one-use consumption before bootstrap work. Never reset on error.
    with engine.begin() as connection:SqlAlchemyPaperStartupRepository(connection).claim_startup(permit)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        handoff.require(migration_compatibility(connection,directory)['state']=='VERIFIED')
        catalog=schema_observation(connection,permit.token['schema_catalog_sha256'])
        handoff.require(catalog['state']=='VERIFIED' and database_identity(connection)==permit.handoff.token['database_sha256'])
    permit.assert_current()
    with engine.begin() as connection:SqlAlchemyPaperStartupRepository(connection).bootstrapped(permit,catalog['sha256'])


def execution_callbacks(engine,permit,*,owner_check=lambda:None):
    handoff.require(type(permit) is authority.Permit and permit.kind=='execution')
    owner_check();permit.assert_current()
    with engine.begin() as connection:SqlAlchemyPaperStartupRepository(connection).claim_execution(permit)
    def check():
        owner_check()
        with engine.connect() as connection:
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            SqlAlchemyPaperStartupRepository(connection).assert_active(permit)
    def transaction_check(connection,phase):
        owner_check();repository=SqlAlchemyPaperStartupRepository(connection);repository.assert_active(permit)
        if phase=='commit':repository.commit_witness(permit,permit.run)
    def complete(run):
        check()
        with engine.begin() as connection:SqlAlchemyPaperStartupRepository(connection).complete(permit,run)
    return check,transaction_check,complete


def _controller_allowed(value,sha,receipt,factory):
    handoff.require(value['plan']==controller.plan(sha,receipt,factory)
        and value['events'][-1]['state'] in {'STAGED','BLOCKED'}
        and value['events'][-1]['code']!='RECOVERY_REQUIRED')


@contextmanager
def startup_owner(directory,root,sha,receipt,factory):
    # Reject interrupted/ordinary verification journals before any recovery or
    # publishing. The established publisher lease owns the entire transition.
    directory=Path(directory);controller._directory(directory,readonly=True)
    _controller_allowed(controller.read(directory/'state.json'),sha,receipt,factory)
    with _activation_guard(Path(root),shared=True),controller.controller(directory,root) as owner:
        _controller_allowed(owner.value,sha,receipt,factory)
        from hope.api.dashboard_activation import Layout,_pointer
        handoff.require(_pointer(Layout(root=Path(root)),'current')==sha)
        owner._check()
        yield owner._check
        owner._check()


def run_startup(args,release,database,stop,*,startup_verifier=authority.verify_startup,execution_verifier=authority.verify_execution):
    """Future operator entry through the existing attested PAPER bootstrap only."""
    release=Path(release);sha=release.name;root=release.parent.parent
    handoff.require(os.environ.get('HOPE_LIVE_ENABLED')=='false' and os.environ.get('HOPE_DEPLOYED_COMMIT')==sha)
    controller.plan(sha,args.deployment_receipt,args.factory)
    handoff.require(0<=args.authority_wait_seconds<=60)
    path=Path(str(args.status_file)+'.startup.json')
    def host_probe():
        from hope.api.deployment_contract import host_observation
        with database().connect() as connection:
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            return host_observation(connection=connection)
    def stage_probe(*a,**kw):
        from hope.api.deployment_contract import inspect_staged_deployment
        return inspect_staged_deployment(*a,**kw,host_probe=host_probe)
    def intent_verifier(*a,**kw):return handoff.verify(*a,**kw,stage_probe=stage_probe)
    with startup_owner(args.controller_directory,root,sha,args.deployment_receipt,args.factory) as owner_check,publisher_lease(path) as lease:
        startup=None;execution=None
        def emit(state):
            lease.publish(path,dict(schema='hope.paper-startup-observation.v1',state=state,release_commit=sha,
                startup_authorization_sha256=None if startup is None else startup.authorization_sha256,
                execution_authorization_sha256=None if execution is None else execution.authorization_sha256,
                generated_at=datetime.now(UTC).isoformat(),execution_authorized=False),publish_service_status)
        deadline=time.monotonic()+args.authority_wait_seconds
        def wait_for(getter,state):
            while True:
                owner_check();handoff.require(not stop.is_set())
                try:return getter()
                except FileNotFoundError:
                    emit(state)
                    if time.monotonic()>=deadline:raise
                    stop.wait(min(.2,max(0,deadline-time.monotonic())))
        try:
            startup=wait_for(lambda:startup_verifier(root,sha,args.startup_request,args.handoff_request,args.deployment_receipt,args.factory,
                args.expected_cycle_key,intent_verifier=intent_verifier),'WAITING_FOR_AUTHORITY')
            owner_check();bootstrap(database(),startup,release/'migrations');emit('BOOTSTRAPPED')
            if args.first_cycle_execution_request is None:return 0
            execution=wait_for(lambda:execution_verifier(root,startup,args.first_cycle_execution_request),'BOOTSTRAPPED')
            check,transaction_check,complete=execution_callbacks(database(),execution,owner_check=owner_check)
            check()
            # Factory code is imported only AFTER distinct execution authority is
            # durably consumed. Reuse the existing certified supervisor/scheduler.
            from hope.api.paper_service import load_plan_factory
            from hope.infrastructure.paper_service import run_service
            factory=load_plan_factory(args.factory,release=release)
            @contextmanager
            def guard():
                check();yield check
                # Completion deliberately closes active admission. Recheck the
                # owner and signatures without treating consumed success as a
                # still-active execution capability.
                owner_check();execution.assert_current()
            def closing_check():
                owner_check()
                with database().connect() as connection:
                    connection.exec_driver_sql('SET TRANSACTION READ ONLY')
                    SqlAlchemyPaperStartupRepository(connection).assert_completed(execution)
            result=run_service(database(),factory,migrations_dir=release/'migrations',status_file=args.status_file,
                stop_event=stop,once=True,poll_seconds=args.poll_seconds,require_certified=True,cycle_guard=guard,
                expected_cycle_key=args.expected_cycle_key,transaction_check=transaction_check,require_new_cycle=True,
                successful_run_observer=complete,cycle_closing_check=closing_check)
            emit('CYCLE_COMPLETED' if result==0 else 'RECOVERY_REQUIRED')
            return result
        except BaseException:
            emit('BLOCKED' if startup is None else 'RECOVERY_REQUIRED')
            raise



def startup_observation(path,expected_release,*,now=None):
    """Bounded live diagnostic, never an execution/readiness capability."""
    from hope.infrastructure.durable_file import bounded_regular_read
    from hope.infrastructure.paper_history import publisher_observation
    from hope.api.snapshot_contract import strict_json
    report=dict(schema='hope.paper-startup-diagnostic.v1',state='UNVERIFIED',code='STARTUP_EVIDENCE_REQUIRED',
        release_commit=None,startup_authorization_sha256=None,execution_authorization_sha256=None,execution_authorized=False)
    try:
        raw=strict_json(bounded_regular_read(path,4096))
        handoff.require(set(raw)=={'schema','state','release_commit','startup_authorization_sha256','execution_authorization_sha256',
            'generated_at','execution_authorized','publisher_instance','publisher_identity_sha256'}
            and raw['schema']=='hope.paper-startup-observation.v1' and raw['release_commit']==handoff.r._sha(expected_release)
            and raw['execution_authorized'] is False and raw['state'] in {'WAITING_FOR_AUTHORITY','BOOTSTRAPPED','CYCLE_COMPLETED','RECOVERY_REQUIRED','BLOCKED'}
            and all(raw[key] is None or handoff.is_hash(raw[key]) for key in ('startup_authorization_sha256','execution_authorization_sha256'))
            and (raw['state']!='CYCLE_COMPLETED' or all(raw[key] is not None for key in ('startup_authorization_sha256','execution_authorization_sha256'))))
        stamp=datetime.fromisoformat(raw['generated_at']);instant=now or datetime.now(UTC)
        handoff.require(stamp.tzinfo is not None and 0<=(instant-stamp).total_seconds())
        if (instant-stamp).total_seconds()>5:return dict(report,state='STALE',code='STARTUP_OBSERVATION_STALE')
        owner=publisher_observation(path,expected_instance=raw['publisher_instance'],expected_identity=raw['publisher_identity_sha256'])
        handoff.require(owner['state']=='VERIFIED')
        return dict(report,state=raw['state'],code='REPORTED_STARTUP_PHASE_ONLY',release_commit=expected_release,
            startup_authorization_sha256=raw['startup_authorization_sha256'],execution_authorization_sha256=raw['execution_authorization_sha256'])
    except Exception:return report
