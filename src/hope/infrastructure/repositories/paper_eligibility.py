"""Durable certified-PAPER eligibility proof and immutable claim receipts."""
from __future__ import annotations

from uuid import UUID
from sqlalchemy import Connection, text

from hope.application.jobs import ScheduledJobRun
from hope.domain.strategy.candidates import StrategyMarket
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository


def _parameters(strategy_version_id, market, expected_classification_id, research_decision_id):
    if not isinstance(strategy_version_id, UUID) or not isinstance(expected_classification_id, UUID):
        raise TypeError("PAPER_STRATEGY_AUTHORIZATION_UUID_REQUIRED")
    if not isinstance(market, StrategyMarket):
        raise TypeError("PAPER_STRATEGY_AUTHORIZATION_MARKET_REQUIRED")
    if not isinstance(research_decision_id, str) or not research_decision_id or research_decision_id != research_decision_id.strip():
        raise ValueError("PAPER_STRATEGY_AUTHORIZATION_DECISION_REQUIRED")
    return dict(strategy=strategy_version_id, market=market.value,
                classification=expected_classification_id, decision=research_decision_id)


def assert_current_eligibility(connection: Connection, strategy_version_id: UUID,
    market: StrategyMarket, expected_classification_id: UUID, research_decision_id: str) -> None:
    """Lock RUNNING control, then eligibility, through the caller's claim/commit.

    READ COMMITTED is required. Demotions and experiment invalidations that
    commit first are rejected; later revocations wait for admitted work to
    commit/roll back. This proof must run BEFORE a new job claim, in the same
    transaction. Certified work records a receipt before writing any effects.
    No TTL or research threshold is introduced here.
    """
    parameters = _parameters(strategy_version_id, market, expected_classification_id, research_decision_id)
    connection.execute(text("SELECT hope_assert_current_paper_eligibility(:strategy,:market,:classification,:decision)"), parameters)


class SqlAlchemyPaperStrategyAuthorizationRepository:
    def __init__(self, connection: Connection):
        self._connection = connection

    def get(self, job_run_id: UUID):
        if not isinstance(job_run_id, UUID):
            raise TypeError("PAPER_STRATEGY_AUTHORIZATION_UUID_REQUIRED")
        return self._connection.execute(text("SELECT job_run_id,strategy_version_id,market,classification_id,research_decision_id,created_at,database_principal FROM paper_strategy_authorizations WHERE job_run_id=:id"), {"id": job_run_id}).mappings().one_or_none()

    def record_for_run(self, run: ScheduledJobRun, strategy_version_id: UUID,
        market: StrategyMarket, expected_classification_id: UUID, research_decision_id: str) -> bool:
        """Append once; authenticate an existing receipt without revoking history.

        A matching historical receipt is not permission to execute a new job.
        The certified binding separately calls assert_current_eligibility before
        its new durable claim. Existing terminal/reconciliation paths only read.
        """
        if not isinstance(run, ScheduledJobRun):
            raise TypeError("PAPER_STRATEGY_AUTHORIZATION_RUN_REQUIRED")
        parameters = _parameters(strategy_version_id, market, expected_classification_id, research_decision_id)
        expected = dict(job_run_id=run.job_run_id, strategy_version_id=strategy_version_id,
            market=market.value, classification_id=expected_classification_id,
            research_decision_id=research_decision_id)
        with self._connection.begin_nested():
            durable = SqlAlchemyJobRunRepository(self._connection).get_record_for_run(run)
            if durable is None or run.job_key.split(":")[:3] != ["paper", market.value, str(strategy_version_id)]:
                raise RuntimeError("PAPER_STRATEGY_AUTHORIZATION_JOB_MISMATCH")
            existing = self.get(run.job_run_id)
            if existing is not None:
                if any(existing[key] != value for key, value in expected.items()):
                    raise RuntimeError("PAPER_STRATEGY_AUTHORIZATION_RECEIPT_CONFLICT")
                return False
            parameters["id"] = run.job_run_id
            inserted = self._connection.execute(text("INSERT INTO paper_strategy_authorizations(job_run_id,strategy_version_id,market,classification_id,research_decision_id) VALUES (:id,:strategy,:market,:classification,:decision) ON CONFLICT(job_run_id) DO NOTHING RETURNING job_run_id"), parameters).scalar_one_or_none()
            existing = self.get(run.job_run_id)
            if existing is None or any(existing[key] != value for key, value in expected.items()):
                raise RuntimeError("PAPER_STRATEGY_AUTHORIZATION_RECEIPT_CONFLICT")
            return inserted is not None
