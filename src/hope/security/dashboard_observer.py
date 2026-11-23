"""Database-only observational qualification; never financial admission.

Expected identities must come from independently reviewed operator configuration.
This does not authenticate the interpreter, release, host PAPER isolation or a
controller. Those existing deployment gates remain mandatory and separate.
"""
from dataclasses import dataclass
import re

from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from hope.security.financial_lifecycle import _require_public_capability_closure


@dataclass(frozen=True)
class ObserverProfile:
    principal: str
    database_sha256: str

    def __post_init__(self):
        if (type(self.principal) is not str
                or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", self.principal) is None
                or type(self.database_sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.database_sha256) is None):
            raise ValueError("DASHBOARD_OBSERVER_PROFILE_INVALID")


def verify_observer(connection: Connection, profile: ObserverProfile) -> str:
    """Inspect the actual READ ONLY session, including reachable SET ROLE rights.

    No SET ROLE, privilege mutation or authority creation is performed. A closing
    recheck bounds privilege drift during collection; it is not a financial
    COMMIT/revocation barrier or proof of an independent runtime session attestor.
    """
    if type(profile) is not ObserverProfile:
        raise ValueError("DASHBOARD_OBSERVER_PROFILE_INVALID")
    row = connection.execute(text(
        "SELECT session_user=current_user AND session_user=:principal, "
        "current_setting('transaction_read_only')='on', "
        "NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE "
        "(r.rolname=current_user OR pg_catalog.pg_has_role(current_user,r.oid,'MEMBER')) AND "
        "(r.rolname LIKE 'pg\\_%' ESCAPE '\\' OR r.rolsuper OR r.rolcreatedb "
        "OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls "
        "OR pg_catalog.has_database_privilege(r.oid,current_database(),'CREATE,TEMPORARY') "
        "OR EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n "
        "WHERE pg_catalog.has_schema_privilege(r.oid,n.oid,'CREATE'))))"
    ), {"principal": profile.principal}).one()
    if tuple(row) != (True, True, True):
        raise ValueError("DASHBOARD_OBSERVER_ROLE_UNSAFE")
    # Reuse the existing PG16/17 column, sequence and non-inherited membership
    # closure for every public relation; do not replace it with table-only ACLs.
    _require_public_capability_closure(connection)
    extra = connection.execute(text(
        "WITH reachable AS MATERIALIZED (SELECT r.oid FROM pg_catalog.pg_roles r "
        "WHERE r.rolname=current_user OR pg_catalog.pg_has_role(current_user,r.oid,'MEMBER')) "
        "SELECT EXISTS(SELECT 1 FROM reachable r CROSS JOIN pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT IN ('public','pg_catalog','information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','f','S') "
        "AND CASE WHEN c.relkind='S' THEN pg_catalog.has_sequence_privilege(r.oid,c.oid,'USAGE,UPDATE') "
        "ELSE pg_catalog.has_table_privilege(r.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') "
        "OR pg_catalog.has_any_column_privilege(r.oid,c.oid,'INSERT,UPDATE,REFERENCES') "
        "OR CASE WHEN pg_catalog.current_setting('server_version_num')::int>=170000 "
        "THEN pg_catalog.has_table_privilege(r.oid,c.oid,'MAINTAIN') ELSE false END END), "
        "EXISTS(SELECT 1 FROM reachable r CROSS JOIN pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND p.prosecdef "
        "AND pg_catalog.has_function_privilege(r.oid,p.oid,'EXECUTE'))"
    )).one()
    if tuple(extra) != (False, False):
        raise ValueError("DASHBOARD_OBSERVER_CAPABILITY_UNSAFE")
    from hope.infrastructure.repositories.paper_handoff import database_identity
    try:
        actual = database_identity(connection)
    except DBAPIError as error:
        raise ValueError("DASHBOARD_OBSERVER_IDENTITY_UNAVAILABLE") from error
    if actual != profile.database_sha256:
        raise ValueError("HOPE_DASHBOARD_DATABASE_IDENTITY_MISMATCH")
    return actual
