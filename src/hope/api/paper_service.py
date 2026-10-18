"""Run the opt-in, PAPER-only autonomous service with a reviewed local factory."""
from __future__ import annotations
import argparse
import importlib
import os
from pathlib import Path
import signal
from threading import Event
from sqlalchemy import create_engine
from hope.infrastructure.paper_service import run_service


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
    args = parser.parse_args(argv)
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        parser.exit(2, "PAPER_SERVICE_DATABASE_CONFIGURATION_REQUIRED\n")
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    try:
        release = Path(args.migrations_dir).resolve(strict=True).parent
        factory = load_plan_factory(args.factory, release=release)
        engine = create_engine(url, pool_pre_ping=True, pool_timeout=10,
            connect_args={"connect_timeout": 10, "options": "-c statement_timeout=30000 -c lock_timeout=10000"})
        try:
            return run_service(engine, factory, migrations_dir=args.migrations_dir,
                status_file=args.status_file, stop_event=stop, poll_seconds=args.poll_seconds, once=args.once,
                require_certified=True)
        finally:
            engine.dispose()
    except Exception:
        parser.exit(2, "PAPER_SERVICE_STARTUP_FAILED\n")


if __name__ == "__main__":
    raise SystemExit(main())
