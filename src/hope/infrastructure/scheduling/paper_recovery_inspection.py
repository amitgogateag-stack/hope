"""Bounded observation of explicit PAPER identities; never recovery permission."""
from itertools import islice
from typing import Iterable
from uuid import UUID

from sqlalchemy import Connection, bindparam, text

from hope.application.jobs import create_scheduled_job_run
from hope.application.paper.effect_topology import classify_paper_reconciliation_effect_topology
from hope.application.paper.effects import PaperEffectType

MAX_INSPECTED_RUNS = 32


def inspect_paper_runs(connection: Connection, run_ids: Iterable[UUID]) -> dict:
    selected = tuple(islice(run_ids, MAX_INSPECTED_RUNS + 1))
    if not selected or len(selected) > MAX_INSPECTED_RUNS:
        raise ValueError("PAPER_INSPECTION_SELECTION_BOUNDS")
    if any(type(value) is not UUID for value in selected) or len(set(selected)) != len(selected):
        raise ValueError("PAPER_INSPECTION_SELECTION_INVALID")
    readonly, isolation = connection.execute(text(
        "SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation')"
    )).one()
    if readonly != "on" or isolation != "repeatable read":
        raise RuntimeError("PAPER_INSPECTION_REQUIRES_READ_ONLY_REPEATABLE_READ")
    control = connection.execute(text(
        "SELECT state FROM paper_environment_control_events ORDER BY control_sequence DESC LIMIT 1"
    )).scalar_one_or_none()
    if control not in ("RUNNING", "HALTED"):
        raise RuntimeError("PAPER_INSPECTION_CONTROL_UNAVAILABLE")
    query = text("""
        SELECT j.job_run_id,j.job_key,j.scheduled_for,j.status,
          EXISTS(SELECT 1 FROM paper_strategy_authorizations a WHERE a.job_run_id=j.job_run_id) AS authorization_present,
          EXISTS(SELECT 1 FROM paper_certified_outcomes o WHERE o.job_run_id=j.job_run_id) AS outcome_present,
          EXISTS(SELECT 1 FROM audit_events a WHERE a.event_type='PAPER_RUN_RECONCILED'
            AND a.entity_type='JOB_RUN' AND a.entity_id=j.job_run_id::text) AS reconciliation_present
        FROM job_runs j WHERE j.job_run_id IN :ids
    """).bindparams(bindparam("ids", expanding=True))
    rows = {row["job_run_id"]: row for row in connection.execute(query, {"ids": selected}).mappings()}
    counts_query = text("""
        SELECT job_run_id,effect_type,count(*) AS total FROM paper_effects
        WHERE job_run_id IN :ids GROUP BY job_run_id,effect_type
    """).bindparams(bindparam("ids", expanding=True))
    counts = {}
    for row in connection.execute(counts_query, {"ids": selected}).mappings():
        counts.setdefault(row["job_run_id"], {})[row["effect_type"]] = row["total"]
    results = []
    for run_id in selected:
        row = rows.get(run_id)
        item = dict(run_id=str(run_id), lifecycle="MISSING", topology="UNASSESSED",
                    effect_counts={}, authorization_present=False, outcome_present=False,
                    reconciliation_present=False, proof="NOT_VERIFIED")
        if row is not None:
            if not row["job_key"].startswith("paper:"):
                item["lifecycle"] = "OUT_OF_SCOPE"
            else:
                try:
                    canonical = create_scheduled_job_run(row["job_key"], row["scheduled_for"])
                    valid = canonical.job_run_id == run_id and row["status"] in ("CLAIMED", "SUCCEEDED", "FAILED")
                except (TypeError, ValueError):
                    valid = False
                item["lifecycle"] = row["status"] if valid else "INVALID"
                if valid:
                    raw = counts.get(run_id, {})
                    # Cardinality classification only needs 0/1/many. Do not
                    # expand attacker-sized counts into an unbounded iterable.
                    try:
                        topology = classify_paper_reconciliation_effect_topology(
                            PaperEffectType(kind) for kind, total in raw.items()
                            for _ in range(min(total, 2)))
                        item["topology"] = topology.value
                        item["effect_counts"] = {kind: raw[kind] for kind in sorted(raw)}
                    except ValueError:
                        item["topology"] = "CONTRADICTORY"
                    for field in ("authorization_present", "outcome_present", "reconciliation_present"):
                        item[field] = bool(row[field])
        results.append(item)
    return dict(schema="hope.paper-recovery-inspection.v1", paper_only=True,
                live_enabled=False, recovery_authorized=False, control_state=control, runs=results)
