"""Read-only EC2 acceptance checks and guarded release-pointer rollback.

No cloud API, migration, role grant or service activation is performed by this CLI.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable

from sqlalchemy import Connection, text


class DeploymentError(RuntimeError):
    pass


def _sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise DeploymentError("OPERATIONS_COMMIT_INVALID")
    return value


def _git(release: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-c", f"safe.directory={release}",
                                 "-C", str(release), *arguments],
                                check=True, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeploymentError("OPERATIONS_RELEASE_GIT_UNAVAILABLE") from exc
    return result.stdout.strip()


def inspect_release(release: Path, expected_sha: str) -> dict[str, object]:
    expected_sha = _sha(expected_sha)
    release = release.resolve(strict=True)
    if Path(_git(release, "rev-parse", "--show-toplevel")).resolve() != release:
        raise DeploymentError("OPERATIONS_RELEASE_ROOT_INVALID")
    if _git(release, "remote", "get-url", "origin") != "https://github.com/amitgogateag-stack/hope.git":
        raise DeploymentError("OPERATIONS_RELEASE_ORIGIN_INVALID")
    if _git(release, "rev-parse", "HEAD") != expected_sha:
        raise DeploymentError("OPERATIONS_RELEASE_HEAD_MISMATCH")
    if _git(release, "status", "--porcelain", "--untracked-files=all"):
        raise DeploymentError("OPERATIONS_RELEASE_DIRTY")
    required = ("src/hope/api/paper_service.py", "src/hope/api/dashboard_server.py",
                "src/hope/api/dashboard_snapshot.py", "docs/dashboard/preview.html",
                "deploy/operations/hope-paper.service")
    if any(not (release / file).is_file() for file in required) or not tuple((release / "migrations").glob("*.sql")):
        raise DeploymentError("OPERATIONS_RELEASE_INCOMPLETE")
    return {"commit": expected_sha, "release": str(release), "status": "COMPATIBLE_CODE"}


def verify_database_role(connection: Connection, *, component: str) -> None:
    """Reject privileged roles and schema owners before service installation.

    Caller must begin a read-only transaction. No role names or URLs are returned.
    """
    if component not in {"paper", "dashboard"}:
        raise DeploymentError("OPERATIONS_COMPONENT_INVALID")
    if connection.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
        raise DeploymentError("OPERATIONS_PREFLIGHT_REQUIRES_READ_ONLY")
    privileged = connection.execute(text(
        "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls "
        "FROM pg_roles WHERE rolname=current_user"
    )).scalar_one()
    owns_schema = connection.execute(text(
        "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='public' "
        "AND pg_has_role(current_user,nspowner,'MEMBER')) OR "
        "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND pg_has_role(current_user,c.relowner,'MEMBER'))"
    )).scalar_one()
    creates_schema = connection.execute(text(
        "SELECT has_schema_privilege(current_user,'public','CREATE') OR "
        "has_database_privilege(current_user,current_database(),'CREATE')"
    )).scalar_one()
    if privileged or owns_schema or creates_schema:
        raise DeploymentError("OPERATIONS_DATABASE_ROLE_PRIVILEGED")
    if component == "dashboard":
        can_write = connection.execute(text(
            "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p') AND "
            "has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES'))"
        )).scalar_one()
        if can_write:
            raise DeploymentError("OPERATIONS_DASHBOARD_ROLE_CAN_WRITE")


@contextmanager
def activation_lock(root: Path):
    """Exclusive operation lease, retained after process death for safe review."""
    path = root.resolve(strict=True) / ".activation.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise DeploymentError("OPERATIONS_ACTIVATION_LOCKED") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        path.unlink()


def switch_release(
    root: Path, target_sha: str, *, expected_current: str,
    accept: Callable[[], None], before_switch: Callable[[], None] = lambda: None,
) -> None:
    """Atomically switch a prepared release and restore pointer on failed acceptance.

    Services must be stopped by the operator first. This never rolls back data.
    The acceptance callback must raise on failed checks. CLI does not expose this
    mutation; the approved deployment wrapper invokes it only after preflight.
    """
    with activation_lock(root):
        _switch_release(root, target_sha, expected_current=expected_current,
                        accept=accept, before_switch=before_switch)


def _switch_release(root, target_sha, *, expected_current, accept, before_switch):
    target_sha, expected_current = _sha(target_sha), _sha(expected_current)
    root = root.resolve(strict=True)
    releases = (root / "releases").resolve(strict=True)
    current = root / "current"
    if not current.is_symlink():
        raise DeploymentError("OPERATIONS_CURRENT_MUST_BE_SYMLINK")
    previous = current.resolve(strict=True)
    target = (releases / target_sha).resolve(strict=True)
    if previous != releases / expected_current or target != releases / target_sha:
        raise DeploymentError("OPERATIONS_RELEASE_LEASE_MISMATCH")
    inspect_release(target, target_sha)
    temporary = root / ".current.next"
    if temporary.exists() or temporary.is_symlink():
        raise DeploymentError("OPERATIONS_SWITCH_ALREADY_PENDING")

    def point(path: Path) -> None:
        os.symlink(path, temporary, target_is_directory=True)
        os.replace(temporary, current)

    try:
        before_switch()
        point(target)
        try:
            accept()
        except BaseException:
            point(previous)
            raise
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect prepared HOPE operations release without deployment")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--database-component", choices=("paper", "dashboard"))
    args = parser.parse_args()
    try:
        result = inspect_release(args.release, args.expected_sha)
        if args.database_component:
            from sqlalchemy import create_engine
            from hope.infrastructure.paper_service import verify_schema_compatibility
            url = os.environ.get("HOPE_DATABASE_URL")
            if not url:
                raise DeploymentError("OPERATIONS_DATABASE_NOT_CONFIGURED")
            engine = create_engine(url, connect_args={"connect_timeout": 10})
            try:
                with engine.connect() as connection:
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                    connection.execute(text("SET LOCAL statement_timeout=15000"))
                    verify_database_role(connection, component=args.database_component)
                    verify_schema_compatibility(connection, args.release / "migrations")
            finally:
                engine.dispose()
            result["database"] = "COMPATIBLE_LEAST_PRIVILEGE"
        print(json.dumps(result, sort_keys=True))
    except Exception:
        # Neither driver exceptions nor subprocess output may disclose credentials.
        print(json.dumps({"status": "BLOCKED", "reason": "OPERATIONS_PREFLIGHT_FAILED"}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
