"""Standalone evidence runner. Unimplemented proofs are failures, never skips.

Run only against a fresh disposable database with SECURITY_DATABASE_URL.
No imports from tests/ or production authority fixtures.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import traceback

import psycopg
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from hope.infrastructure.postgres.migrations import apply_migrations, migration_inventory

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("SECURITY_EVIDENCE_DIR", "security-evidence"))


def run() -> int:
    OUT.mkdir(exist_ok=True, parents=True)
    requirements = json.loads((ROOT / "security/requirements.json").read_text())
    if len(requirements) != 20 or len(set(requirements)) != 20:
        raise RuntimeError("mandatory test inventory invalid")
    report = {"a1": "OPEN", "requirements": {name: {"status": "FAIL", "reason":
        "Independent positive admission/session attestor unavailable; scenario proof not executed"}
        for name in requirements}, "experiments": [], "production_verification": False}
    try:
        url = make_url(os.environ["SECURITY_DATABASE_URL"])
        with psycopg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False), autocommit=True) as admin:
            major = admin.info.server_version // 10000
            report["postgres_major"] = major
            if str(major) != os.environ["SECURITY_POSTGRES_MAJOR"] or major not in (16, 17):
                raise RuntimeError("wrong PostgreSQL major")
            # Refuse a populated database: never destroy a schema to run tests.
            if admin.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]:
                raise RuntimeError("fresh disposable database required")
            credentials = {}
            suffix = secrets.token_hex(8)
            for kind in ("owner", "verifier", "worker"):
                role, password = "security_" + kind + "_" + suffix, secrets.token_urlsafe(32)
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT").format(sql.Identifier(role), sql.Literal(password)))
                credentials[kind] = url.set(username=role, password=password)
            owner = credentials["owner"].username
            admin.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(owner)))
            admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            admin.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(url.database)))
            engine = create_engine(credentials["owner"])
            with engine.begin() as connection:
                apply_migrations(connection, ROOT / "migrations")
                assert apply_migrations(connection, ROOT / "migrations") == []
            engine.dispose()
            report["migration_checksums"] = {m.name: m.checksum for m in migration_inventory(ROOT / "migrations")}
            for kind in ("worker", "verifier"):
                role = credentials[kind].username
                admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
                admin.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(role)))
            worker = credentials["worker"].username
            # Deliberately broad accidental grant: triggers must still contain it.
            admin.execute(sql.SQL("GRANT INSERT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(worker)))
            attacks = {
                "self_classify": "INSERT INTO paper_admission_principals SELECT oid,rolname,'ORDINARY',clock_timestamp() FROM pg_roles WHERE rolname=session_user",
                "self_approve": "INSERT INTO paper_admission_approvals VALUES ('paper_startup_claims',repeat('a',64),(SELECT oid FROM pg_roles WHERE rolname=session_user),'{}',extract(epoch FROM clock_timestamp())::bigint+30,clock_timestamp())",
                "self_superuser": sql.SQL("ALTER ROLE {} SUPERUSER").format(sql.Identifier(worker)),
                "impersonate_verifier": sql.SQL("SET ROLE {}").format(sql.Identifier(credentials["verifier"].username)),
                "direct_financial_insert": "INSERT INTO paper_portfolios(portfolio_id,initial_cash,cash) VALUES ('00000000-0000-0000-0000-000000000001',50,50)",
            }
            with psycopg.connect(credentials["worker"].set(drivername="postgresql").render_as_string(hide_password=False), autocommit=True) as connection:
                for name, attack in attacks.items():
                    try:
                        connection.execute(attack)
                    except psycopg.Error as exc:
                        if exc.sqlstate not in ("23514", "42501"):
                            raise RuntimeError("unexpected attack failure: " + str(exc.sqlstate)) from None
                        report["experiments"].append({"name": name, "status": "PASS", "sqlstate": exc.sqlstate})
                    else:
                        raise RuntimeError("unauthorized SQL accepted: " + name)
                for table in ("paper_portfolios", "paper_effects", "orders", "fills", "paper_admission_approvals", "paper_admission_principals"):
                    assert connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0] == 0
            result = subprocess.run([sys.executable, "-m", "hope.security.admission_verifier"], input=b'{"verified":true}', capture_output=True, timeout=10, check=False)
            evidence = json.loads(result.stdout)
            assert result.returncode == 78 and evidence["decision"] == "DENY" and evidence["approval_issued"] is False
            report["experiments"].append({"name": "missing_trusted_attestor_denied", "status": "PASS"})
            report["separation"] = "three fresh LOGINs; verifier has no approval-write privilege; issuance disabled"
    except Exception as exc:
        # Do not serialize exception messages that might contain connection secrets.
        report["runner_error"] = type(exc).__name__
        report["error_frames"] = [f.name for f in traceback.extract_tb(exc.__traceback__)]
    finally:
        (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        lines = ["# Independent admission security evidence", "", "A1 OPEN; engineering and operational PAPER NO-GO.",
            "All 20 required scenario proofs remain FAIL until a trusted positive session attestor exists.",
            "Denial experiments below are containment evidence only.", ""]
        lines.extend(f"- {x['name']}: {x['status']}" for x in report["experiments"])
        if "runner_error" in report:
            lines.append("Runner infrastructure failure: " + report["runner_error"])
        (OUT / "report.md").write_text("\n".join(lines) + "\n")
    # A containment pass must not make the admission acceptance gate green.
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
