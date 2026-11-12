"""Read-only integration boundary; never an approval issuer or trading adapter.

References are operator expectations, NOT authenticated runtime/session evidence.
History consistency does not authorize another cycle, including after unknown COMMIT.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from uuid import UUID

from sqlalchemy import Engine, text


class LifecycleDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class CycleReference:
    database_sha256: str
    authorization_sha256: str
    job_run_id: UUID
    financial_xid: str
    financial_principal: str
    portfolio_id: UUID

    def __post_init__(self) -> None:
        for value in (self.database_sha256, self.authorization_sha256):
            if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
                raise LifecycleDenied('REFERENCE_DIGEST_INVALID')
        if type(self.job_run_id) is not UUID or type(self.portfolio_id) is not UUID:
            raise LifecycleDenied('REFERENCE_IDENTITY_INVALID')
        if (type(self.financial_xid) is not str
                or re.fullmatch('[1-9][0-9]{0,19}', self.financial_xid) is None
                or int(self.financial_xid) > 2**64-1):
            raise LifecycleDenied('REFERENCE_TRANSACTION_INVALID')
        if (type(self.financial_principal) is not str
                or not 1 <= len(self.financial_principal.encode()) <= 63
                or self.financial_principal != self.financial_principal.strip()
                or any(ord(c) < 32 for c in self.financial_principal)):
            raise LifecycleDenied('REFERENCE_PRINCIPAL_INVALID')


def dispatch_one_cycle(request: object) -> None:
    """Permanently disabled interface: no engine, SQL, factory, callback or Permit.

    Even a well-shaped history report/signature/worker-supplied permit cannot
    become authority. A reviewed independent root/session protocol is missing.
    """
    raise LifecycleDenied('INDEPENDENT_FINANCIAL_ADMISSION_UNAVAILABLE')


# Literal observer scope, never supplied by a worker. Includes every financial
# relation guarded by migration236 and the authority/audit relations read below.
RELATIONS = (
    'signals', 'orders', 'fills', 'paper_effects', 'paper_portfolios',
    'paper_portfolio_positions', 'paper_portfolio_fill_applications',
    'paper_portfolio_pnl_events', 'paper_risk_assessments',
    'paper_order_terminal_events', 'paper_no_trade_decisions',
    'paper_strategy_authorizations', 'paper_certified_outcomes', 'job_runs',
    'audit_events', 'paper_startup_executions', 'paper_startup_completions',
    'paper_startup_commit_witnesses', 'paper_startup_financial_transactions',
)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise LifecycleDenied(reason)


def inspect_committed_cycle(engine: Engine, reference: CycleReference) -> dict:
    """Inspect one real committed lifecycle in a bounded READ ONLY snapshot.

    Requires a genuine restricted observer LOGIN without reachable DML/DDL or
    privileged role membership. All names are fixed/public-pinned. The report
    is diagnostic only and always execution_authorized=false. SQL/connection
    failures propagate; they cannot become a complete-history result.
    """
    _require(type(reference) is CycleReference, 'REFERENCE_TYPE_INVALID')
    _require(engine.dialect.name == 'postgresql', 'POSTGRESQL_REQUIRED')
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            connection.exec_driver_sql('SET LOCAL search_path=pg_catalog,public,pg_temp')
            connection.exec_driver_sql("SET LOCAL statement_timeout='5000ms'")
            connection.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
            connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='5000ms'")
            role = connection.execute(text(
                "SELECT session_user=current_user, current_setting('transaction_read_only')='on', "
                "NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE "
                "(r.rolname=current_user OR pg_has_role(current_user,r.oid,'MEMBER')) AND "
                "(r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls "
                "OR has_database_privilege(r.oid,current_database(),'CREATE,TEMPORARY') "
                "OR EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n "
                "WHERE has_schema_privilege(r.oid,n.oid,'CREATE')))), session_user"
            )).one()
            _require(tuple(role[:3]) == (True, True, True), 'OBSERVER_ROLE_UNSAFE')
            catalog = connection.execute(text(
                "SELECT c.relname,c.relkind,c.relrowsecurity, "
                "has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE'), "
                "has_table_privilege(current_user,c.oid,'SELECT') "
                "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=ANY(:relations)"
            ), {'relations': list(RELATIONS)}).all()
            _require(len(catalog) == len(RELATIONS) and all(
                r[1] == 'r' and r[2] is False and r[3] is False and r[4] is True
                for r in catalog), 'OBSERVER_RELATION_BOUNDARY_UNSAFE')
            # Imports are read-only repository/domain logic, never a work factory.
            from hope.infrastructure.repositories.paper_handoff import database_identity
            actual_database = database_identity(connection)
            _require(actual_database == reference.database_sha256, 'DATABASE_IDENTITY_MISMATCH')
            row = connection.execute(text(
                "SELECT e.job_run_id,e.job_key,e.scheduled_for,e.database_sha256,e.database_principal, "
                "t.transaction_id::text,w.financial_transaction_id::text, "
                "t.database_principal,w.database_principal,c.database_principal, "
                "j.status,o.outcome,o.inputs_hash, "
                "w.job_run_id=e.job_run_id AND c.job_run_id=e.job_run_id, "
                "e.paper_only AND NOT e.live_enabled AND e.max_cycles=1, "
                "e.evidence_sha256=w.evidence_sha256 AND e.evidence_sha256=c.evidence_sha256, "
                "e.controller_process_sha256=w.controller_process_sha256 "
                "AND e.controller_process_sha256=c.controller_process_sha256, "
                "e.policy_authority_sha256=w.policy_authority_sha256 "
                "AND e.policy_authority_sha256=c.policy_authority_sha256 "
                "FROM public.paper_startup_executions e "
                "JOIN public.paper_startup_financial_transactions t USING(authorization_sha256) "
                "JOIN public.paper_startup_commit_witnesses w USING(authorization_sha256) "
                "JOIN public.paper_startup_completions c USING(authorization_sha256) "
                "JOIN public.job_runs j ON j.job_run_id=e.job_run_id "
                "JOIN public.paper_certified_outcomes o ON o.job_run_id=j.job_run_id "
                "JOIN public.paper_strategy_authorizations a ON a.job_run_id=j.job_run_id "
                "WHERE e.authorization_sha256=:authorization AND e.job_run_id=:job"
            ), {'authorization': reference.authorization_sha256, 'job': reference.job_run_id}).one_or_none()
            if row is None:
                raise LifecycleDenied('LIFECYCLE_INCOMPLETE')
            _require(row[3] == actual_database and row[5] == row[6] == reference.financial_xid,
                     'FINANCIAL_TRANSACTION_MISMATCH')
            _require(all(row[i] == reference.financial_principal for i in (4, 7, 8, 9)),
                     'FINANCIAL_PRINCIPAL_MISMATCH')
            _require(row[10] == 'SUCCEEDED' and all(row[i] is True for i in range(13, 18)),
                     'LIFECYCLE_BINDING_MISMATCH')
            from hope.application.jobs import create_scheduled_job_run
            from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
            from hope.infrastructure.repositories.paper_effects import SqlAlchemyPaperEffectRepository
            from hope.infrastructure.repositories.paper_reconciliation_audit import SqlAlchemyPaperReconciliationAuditRepository
            run = create_scheduled_job_run(row[1], row[2])
            _require(run.job_run_id == reference.job_run_id, 'JOB_IDENTITY_MISMATCH')
            completion = SqlAlchemyJobRunRepository(connection).get_record_for_run(run)
            expected = ['FILL', 'ORDER', 'PNL', 'RISK', 'SIGNAL'] if row[11] == 'FILLED' else ['NO_TRADE']
            total = connection.execute(text('SELECT count(*) FROM public.paper_effects WHERE job_run_id=:job'),
                {'job': reference.job_run_id}).scalar_one()
            _require(total == len(expected), 'FINANCIAL_WRITE_SET_INCOMPLETE')
            effects = SqlAlchemyPaperEffectRepository(connection).list_for_job_run(reference.job_run_id)
            _require(row[11] in {'FILLED', 'NO_TRADE', 'RISK_BLOCKED'}
                     and sorted(e.effect_type.value for e in effects) == expected, 'FINANCIAL_WRITE_SET_INCOMPLETE')
            _require(completion is not None and SqlAlchemyPaperReconciliationAuditRepository(connection).verify(
                completion, effects), 'RECONCILIATION_AUDIT_INCOMPLETE')
            if row[11] == 'FILLED':
                ids = {e.effect_type.value.lower(): e.entity_id for e in effects}
                ids['portfolio'] = reference.portfolio_id
                ids['inputs'] = row[12]
                linked = connection.execute(text(
                    "SELECT count(*) FROM public.fills f JOIN public.orders o ON o.order_id=f.order_id "
                    "JOIN public.signals s ON s.signal_id=o.signal_id "
                    "JOIN public.paper_risk_assessments r ON r.signal_id=s.signal_id "
                    "JOIN public.paper_portfolio_fill_applications a ON a.fill_id=f.fill_id "
                    "JOIN public.paper_portfolio_pnl_events p ON p.fill_id=f.fill_id AND p.portfolio_id=a.portfolio_id "
                    "JOIN public.paper_portfolios b ON b.portfolio_id=a.portfolio_id "
                    "WHERE f.fill_id=:fill AND o.order_id=:order AND s.signal_id=:signal "
                    "AND r.signal_id=:risk AND p.pnl_event_id=:pnl AND p.portfolio_id=:portfolio "
                    "AND s.inputs_hash=:inputs"
                ), ids).scalar_one()
                _require(linked == 1, 'FINANCIAL_ENTITY_LINKAGE_INCOMPLETE')
            else:
                linked = connection.execute(text(
                    "SELECT count(*) FROM public.paper_no_trade_decisions "
                    "WHERE job_run_id=:job AND inputs_hash=:inputs"
                ), {'job': reference.job_run_id, 'inputs': row[12]}).scalar_one()
                _require(linked == 1, 'NO_TRADE_LINKAGE_INCOMPLETE')
            observation = {
                'schema': 'hope.financial-lifecycle-observation.v1',
                'scope': 'READ_ONLY_COMMITTED_HISTORY_NOT_ADMISSION',
                'history_consistent': True, 'execution_authorized': False,
                'production_verification': False, 'replay_permitted': False,
                'database_sha256': actual_database, 'job_run_id': str(reference.job_run_id),
                'authorization_sha256': reference.authorization_sha256,
                'financial_xid': reference.financial_xid, 'observer_principal': role[3],
                'outcome': row[11], 'effects': [str(e.effect_id) for e in effects],
                'independent_session_attestation': 'BLOCKED',
            }
            observation['observation_sha256'] = sha256(json.dumps(
                observation, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            return observation
