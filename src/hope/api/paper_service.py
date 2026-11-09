"""Run the opt-in, PAPER-only autonomous service with a reviewed local factory."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import importlib
import os
import re
from pathlib import Path
import signal
from hope.infrastructure.signal_stop import SignalStop as Event
from sqlalchemy import create_engine
from hope.infrastructure.paper_service import run_service,publish_startup_failure
from hope.api.paper_operational_guard import paper_operational_guard


def load_plan_factory(specification: str, *, release: str | Path | None = None):
    if specification.count(":") != 1:
        raise ValueError("PAPER_SERVICE_FACTORY_FORMAT_INVALID")
    module, name = specification.split(":")
    if not module or not name or not all(part.isidentifier() for part in module.split(".")) or not name.isidentifier():
        raise ValueError("PAPER_SERVICE_FACTORY_FORMAT_INVALID")
    imported = importlib.import_module(module)
    factory = getattr(imported, name)
    if not callable(factory):
        raise ValueError("PAPER_SERVICE_FACTORY_NOT_CALLABLE")
    if release is not None:
        release_src = (Path(release).resolve(strict=True) / "src").resolve(strict=True)
        source = getattr(imported, "__file__", None)
        if (not isinstance(source, str) or getattr(factory, "__module__", None) != module):
            raise ValueError("PAPER_SERVICE_FACTORY_RELEASE_MISMATCH")
        try:
            source = Path(source).resolve(strict=True)
            source.relative_to(release_src)
        except (OSError, ValueError):
            raise ValueError("PAPER_SERVICE_FACTORY_RELEASE_MISMATCH") from None
    return factory


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="HOPE PAPER service; requires reviewed real-data factory")
    parser.add_argument("--factory", required=True)
    parser.add_argument("--migrations-dir", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--poll-seconds", type=float, default=30)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--deployment-receipt",help="Independently approved immutable receipt SHA256; fail closed on absent evidence")
    parser.add_argument("--controller-directory",type=Path,help="Optional staged controller admission gate; requires --deployment-receipt")
    parser.add_argument('--expected-cycle-key',help='Additional one-run constraint; never substitutes for independently approved execution authority')
    parser.add_argument('--handoff-request',help='One-use intent admission in addition to existing running authority; requires exact single-cycle mode')
    parser.add_argument('--startup-request',help='Separately signed bootstrap-only authority; never implicit from STAGED')
    parser.add_argument('--first-cycle-execution-request',help='Distinct signed one-certified-cycle authority')
    parser.add_argument('--authority-wait-seconds',type=int,default=0)
    args = parser.parse_args(argv)
    if args.startup_request is not None and (not args.once or args.handoff_request is None or args.expected_cycle_key is None
            or args.deployment_receipt is None or args.controller_directory is None
            or re.fullmatch('[0-9a-f]{64}',args.startup_request) is None or not 0<=args.authority_wait_seconds<=60):
        parser.exit(2,'PAPER_STARTUP_ARGUMENTS_INVALID\n')
    if (args.first_cycle_execution_request is not None and (args.startup_request is None
            or re.fullmatch('[0-9a-f]{64}',args.first_cycle_execution_request) is None)) or (args.startup_request is None and args.authority_wait_seconds!=0):
        parser.exit(2,'PAPER_STARTUP_ARGUMENTS_INVALID\n')
    if args.handoff_request is not None and (args.expected_cycle_key is None or re.fullmatch('[0-9a-f]{64}',args.handoff_request) is None):
        parser.exit(2,'PAPER_HANDOFF_REQUEST_INVALID\n')
    if args.expected_cycle_key is not None and (not args.once or args.controller_directory is None
            or args.deployment_receipt is None or re.fullmatch('[0-9a-f]{64}',args.expected_cycle_key) is None):
        parser.exit(2,'PAPER_SINGLE_CYCLE_GUARD_REQUIRED\n')
    if args.deployment_receipt is not None and not re.fullmatch('[0-9a-f]{64}',args.deployment_receipt):
        parser.exit(2,"PAPER_SERVICE_DEPLOYMENT_RECEIPT_INVALID\n")
    if args.controller_directory is not None and args.deployment_receipt is None:
        parser.exit(2,"PAPER_SERVICE_CONTROLLER_RECEIPT_REQUIRED\n")
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        parser.exit(2, "PAPER_SERVICE_DATABASE_CONFIGURATION_REQUIRED\n")
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    engine=None
    def database():
        nonlocal engine
        if engine is None:
            options="-c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=5000"
            if args.startup_request is not None:options+=" -c search_path=pg_catalog,public,pg_temp"
            engine=create_engine(url,pool_pre_ping=True,pool_timeout=10,
                connect_args={"connect_timeout":10,"options":options})
        return engine
    try:
        release = Path(args.migrations_dir).resolve(strict=True).parent
        if args.startup_request is not None:
            from hope.api.paper_startup import run_startup
            return run_startup(args,release,database,stop)
        def host_probe():
            # Called only after source/runtime/image/provider approvals pass.
            # Observe the configured database without any data mutation.
            from hope.api.deployment_contract import host_observation
            with database().connect() as connection:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                return host_observation(connection=connection)
        def guard():
            if args.deployment_receipt is None:return paper_operational_guard(release)
            options={} if args.controller_directory is None else {'controller_directory':args.controller_directory}
            return paper_operational_guard(release,deployment_receipt=args.deployment_receipt,factory_spec=args.factory,host_probe=host_probe,**options)
        @contextmanager
        def execution_guard():
            with guard() as runtime_check:
                if args.handoff_request is None:
                    yield runtime_check
                else:
                    from hope.api.paper_handoff import handoff_guard
                    with handoff_guard(database(),release.parent.parent,release.name,args.handoff_request,
                                       args.deployment_receipt,args.factory,args.expected_cycle_key) as handoff_check:
                        def check():
                            runtime_check();handoff_check()
                        check();yield check;check()
        with guard():
            factory = load_plan_factory(args.factory, release=release)
        database()
        return run_service(engine, factory, migrations_dir=args.migrations_dir,
            status_file=args.status_file, stop_event=stop, poll_seconds=args.poll_seconds, once=args.once,
            require_certified=True,cycle_guard=execution_guard,
            **({"expected_cycle_key":args.expected_cycle_key} if args.expected_cycle_key is not None else {}))
    except Exception:
        # The helper acquires publication ownership independently. A rejected
        # competing CLI must leave the active worker's health/history untouched.
        publish_startup_failure(args.status_file,os.getenv("HOPE_DEPLOYED_COMMIT"))
        parser.exit(2, "PAPER_SERVICE_STARTUP_FAILED\n")
    finally:
        if engine is not None:engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
