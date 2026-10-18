"""Transaction-local database wait bounds for explicit PAPER recovery only."""
from contextlib import contextmanager

from sqlalchemy import Connection, text


RECOVERY_LOCK_TIMEOUT_MS = 5_000
RECOVERY_STATEMENT_TIMEOUT_MS = 30_000


@contextmanager
def bounded_paper_recovery(connection: Connection):
    """Own the recovery savepoint and preserve stricter caller timeouts.

    PostgreSQL restores SET LOCAL settings on savepoint rollback, including
    cancellation and backend errors. Restore explicitly on successful release so
    pooled connections and subsequent caller work never inherit these bounds.
    These are per-lock/per-statement limits, not a total batch wall-clock SLA.
    No session-level setting or caller transaction is committed here.
    """
    with connection.begin_nested():
        previous = dict(connection.execute(text(
            "SELECT name, setting FROM pg_settings "
            "WHERE name IN ('lock_timeout', 'statement_timeout')"
        )).all())
        caps = {"lock_timeout": RECOVERY_LOCK_TIMEOUT_MS,
                "statement_timeout": RECOVERY_STATEMENT_TIMEOUT_MS}
        if set(previous) != set(caps):
            raise RuntimeError("PAPER_RECOVERY_TIMEOUT_SETTINGS_UNAVAILABLE")
        bounded = {}
        for name, cap in caps.items():
            value = int(previous[name])  # pg_settings reports these in milliseconds.
            if value < 0:
                raise RuntimeError("PAPER_RECOVERY_TIMEOUT_SETTINGS_INVALID")
            bounded[name] = str(min(value, cap) if value else cap) + "ms"
        configure = text("SELECT set_config('lock_timeout', :lock_timeout, true), "
                         "set_config('statement_timeout', :statement_timeout, true)")
        connection.execute(configure, bounded)
        yield
        connection.execute(configure, {name: str(value) + "ms" for name, value in previous.items()})


def recovery_database_error_code(error, prefix: str) -> str:
    """Classify server cancellation without exposing SQL, parameters or DSNs."""
    state = getattr(getattr(error, "orig", None), "sqlstate", None)
    return prefix + ("_DATABASE_TIMEOUT" if state in {"55P03", "57014"}
                     else "_DATABASE_UNAVAILABLE")
