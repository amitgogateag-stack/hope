"""Explicit historical recovery; never a new execution authorization."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice
from typing import Iterable
from uuid import UUID

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError
from hope.infrastructure.scheduling.paper_recovery_limits import (
    bounded_paper_recovery, recovery_database_error_code,
)

from hope.application.experiments.config_hash import configuration_hash
from hope.application.jobs import JobRunStatus, create_scheduled_job_run
from hope.infrastructure.certified_paper import CertifiedPaperSettings
from hope.infrastructure.repositories.jobs import SqlAlchemyJobRunRepository
from hope.infrastructure.repositories.paper_control import SqlAlchemyPaperEnvironmentControlRepository
from hope.infrastructure.scheduling.paper_reconciliation import (
    _reconcile_completed_paper_run, authenticate_successful_paper_run,
)

MAX_CERTIFIED_RECOVERY_RUNS = 32


@dataclass(frozen=True)
class CertifiedPaperRecoveryResult:
    reconciled_run_ids: tuple[UUID, ...]
    authenticated_run_ids: tuple[UUID, ...]


def _historical_receipt(connection: Connection, run) -> None:
    row = connection.execute(text("""
        SELECT a.strategy_version_id,a.market,a.research_decision_id,a.created_at AS authorized_at,
               a.database_principal,c.state,c.strategy_version_id AS classified_version,
               c.research_decision_id AS classified_decision,c.markets,
               rd.decision,rd.variant_run_id,o.source_run_id,o.dataset_version_id,
               o.universe_version_id,o.configuration_hash,o.inputs_hash,o.decision_time,
               o.outcome,o.reason_code,o.created_at AS outcome_at,
               e.strategy_version_id AS researched_version,e.configuration_hash AS researched_configuration,
               e.universe_version_id AS researched_universe,cs.canonical_json,
               v.immutable,d.pit_certified,d.source,rdataset.source AS research_source,
               m.universe_version_id AS manifest_universe,
               n.inputs_hash AS no_trade_inputs,s.inputs_hash AS signal_inputs,
               s.decision_time AS signal_time,app.portfolio_id,p.initial_cash
          FROM paper_strategy_authorizations a
          JOIN strategy_candidate_classifications c USING(classification_id)
          JOIN research_decisions rd ON rd.decision_id=a.research_decision_id
          JOIN paper_certified_outcomes o USING(job_run_id)
          JOIN research_runs r ON r.research_run_id=o.source_run_id
          JOIN experiments e ON e.experiment_id=r.experiment_id
          JOIN configuration_snapshots cs ON cs.configuration_hash=o.configuration_hash
          JOIN dataset_versions v ON v.dataset_version_id=o.dataset_version_id
          JOIN datasets d ON d.dataset_id=v.dataset_id
          JOIN dataset_versions rv ON rv.dataset_version_id=e.dataset_version_id
          JOIN datasets rdataset ON rdataset.dataset_id=rv.dataset_id
          JOIN market_data_coverage_manifests m ON m.dataset_version_id=v.dataset_version_id
          LEFT JOIN paper_no_trade_decisions n USING(job_run_id)
          LEFT JOIN paper_effects se ON se.job_run_id=a.job_run_id AND se.effect_type='SIGNAL'
          LEFT JOIN signals s ON s.signal_id=se.entity_id
          LEFT JOIN paper_effects fe ON fe.job_run_id=a.job_run_id AND fe.effect_type='FILL'
          LEFT JOIN paper_portfolio_fill_applications app ON app.fill_id=fe.entity_id
          LEFT JOIN paper_portfolios p ON p.portfolio_id=app.portfolio_id
         WHERE a.job_run_id=:id
    """), {"id": run.job_run_id}).mappings().one_or_none()
    error = "PAPER_CERTIFIED_RECOVERY_RECEIPT_MISMATCH"
    if row is None:
        raise RuntimeError(error)
    scope = run.job_key.split(":")
    if (len(scope) != 4 or scope[:3] != ["paper", row["market"], str(row["strategy_version_id"])]
            or row["state"] != "OPERATIONAL_CANDIDATE" or row["decision"] != "SUPPORTED"
            or row["strategy_version_id"] != row["classified_version"]
            or row["strategy_version_id"] != row["researched_version"]
            or row["research_decision_id"] != row["classified_decision"]
            or row["market"] not in row["markets"]
            or row["source_run_id"] != row["variant_run_id"]
            or row["configuration_hash"] != row["researched_configuration"]
            or row["universe_version_id"] != row["researched_universe"]
            or row["universe_version_id"] != row["manifest_universe"]
            or row["immutable"] is not True or row["pit_certified"] is not True
            or row["source"] != row["research_source"]
            or not row["database_principal"] or row["authorized_at"] > row["outcome_at"]):
        raise RuntimeError(error)
    config = row["canonical_json"]
    if not isinstance(config, dict) or configuration_hash(config) != row["configuration_hash"]:
        raise RuntimeError(error)
    settings = CertifiedPaperSettings.model_validate(config.get("paper_execution"))
    expected_time = run.scheduled_for.astimezone(UTC) - timedelta(
        milliseconds=settings.latency_milliseconds + settings.submission_delay_milliseconds)
    if settings.market.value != row["market"] or row["decision_time"].astimezone(UTC) != expected_time:
        raise RuntimeError(error)
    if row["outcome"] == "FILLED":
        if (row["reason_code"] != "PORTFOLIO_RISK_APPROVED" or row["inputs_hash"] != row["signal_inputs"]
                or row["signal_time"] != row["decision_time"] or row["no_trade_inputs"] is not None
                or row["portfolio_id"] != settings.portfolio_id or row["initial_cash"] != settings.initial_cash):
            raise RuntimeError(error)
    elif row["outcome"] in ("NO_TRADE", "RISK_BLOCKED"):
        if (row["inputs_hash"] != row["no_trade_inputs"] or row["signal_inputs"] is not None
                or not row["reason_code"] or (row["outcome"] == "NO_TRADE" and row["reason_code"] != "NO_SIGNAL")):
            raise RuntimeError(error)
    else:
        raise RuntimeError(error)


def recover_certified_paper_batch(connection: Connection, run_ids: Iterable[UUID], *,
                                 current: datetime) -> CertifiedPaperRecoveryResult:
    """Authenticate/reconcile up to 32 explicit committed runs atomically.

    No discovery, schedule, factory, strategy invocation, claim or economic writer
    is reachable. Historical permission survives subsequent demotion/revocation;
    this operation cannot confer permission on a missing, partial or fresh run.
    Caller owns the outer transaction; success is provisional until commit.
    """
    selected = tuple(islice(run_ids, MAX_CERTIFIED_RECOVERY_RUNS + 1))
    if not selected or len(selected) > MAX_CERTIFIED_RECOVERY_RUNS:
        raise ValueError("PAPER_CERTIFIED_RECOVERY_SELECTION_BOUNDS")
    if any(type(value) is not UUID for value in selected) or len(set(selected)) != len(selected):
        raise ValueError("PAPER_CERTIFIED_RECOVERY_SELECTION_INVALID")
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("PAPER_CERTIFIED_RECOVERY_CLOCK_INVALID")
    reconciled, authenticated = [], []
    try:
        with bounded_paper_recovery(connection):
            SqlAlchemyPaperEnvironmentControlRepository(connection).assert_running()
            records = []
            repository = SqlAlchemyJobRunRepository(connection)
            for run_id in sorted(selected, key=lambda value: value.int):
                connection.execute(text("SELECT job_run_id FROM job_runs WHERE job_run_id=:id FOR UPDATE"), {"id": run_id})
                record = repository.get_record(run_id)
                if record is None or record.status not in (JobRunStatus.CLAIMED, JobRunStatus.SUCCEEDED):
                    raise RuntimeError("PAPER_CERTIFIED_RECOVERY_COMMITTED_RUN_REQUIRED")
                run = create_scheduled_job_run(record.run.job_key, record.run.scheduled_for)
                if run.job_run_id != run_id or current.astimezone(UTC) < run.scheduled_for.astimezone(UTC):
                    raise RuntimeError("PAPER_CERTIFIED_RECOVERY_RUN_IDENTITY_MISMATCH")
                _historical_receipt(connection, run)
                records.append(record)
            # Completed in-flight owners must terminalize first so verification
            # of an older portfolio receipt sees the final selected ownership.
            for record in sorted(records, key=lambda value: value.status is not JobRunStatus.CLAIMED):
                if record.status is JobRunStatus.CLAIMED:
                    _reconcile_completed_paper_run(connection, record.run, current=current)
                    reconciled.append(record.run.job_run_id)
                else:
                    authenticate_successful_paper_run(connection, record.run)
                    authenticated.append(record.run.job_run_id)
    except SQLAlchemyError as exc:
        raise RuntimeError(recovery_database_error_code(exc, "PAPER_CERTIFIED_RECOVERY")) from exc
    return CertifiedPaperRecoveryResult(tuple(reconciled), tuple(authenticated))
