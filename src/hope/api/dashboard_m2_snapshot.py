"""Bounded M2 aggregate readers, called inside the dashboard read-only snapshot."""
from sqlalchemy import text

from hope.api.dashboard_projection import BLOCKED_CODES, CERTIFIED_OUTCOMES, FAMILY_CODES, RISK_BLOCK_REASONS


def readers(connection, generated):
    def strategy_catalog():
        total = connection.execute(text("SELECT count(*) FROM strategy_versions")).scalar_one()
        rows = connection.execute(text(
            "SELECT v.strategy_version_id,s.family,c.state,c.markets FROM strategy_versions v "
            "JOIN strategies s ON s.strategy_id=v.strategy_id "
            "LEFT JOIN current_strategy_candidate_classifications c ON c.strategy_version_id=v.strategy_version_id "
            "ORDER BY v.strategy_version_id LIMIT 20"
        )).all()
        return {"total": total, "truncated": total > 20, "rows": [
            {"strategy_version_id": str(version), "family_code": family if family in FAMILY_CODES else "OTHER",
             "candidate_state": state or "UNCLASSIFIED", "markets": list(markets or [])}
            for version, family, state, markets in rows
        ]}
    def dataset_certification():
        total, sealed, certified, manifested = connection.execute(text(
            "SELECT count(*),count(*) FILTER(WHERE v.immutable),"
            "count(*) FILTER(WHERE v.immutable AND d.pit_certified),"
            "count(*) FILTER(WHERE v.immutable AND EXISTS (SELECT 1 FROM market_data_coverage_manifests m "
            "WHERE m.dataset_version_id=v.dataset_version_id)) FROM dataset_versions v JOIN datasets d USING(dataset_id)"
        )).one()
        return dict(version_total=total, sealed_versions=sealed, pit_certified_versions=certified, manifest_backed_sealed_versions=manifested)
    def execution_ledger():
        values = connection.execute(text(
            "WITH paper_fills AS (SELECT f.fill_id FROM fills f JOIN orders o USING(order_id) WHERE o.environment='PAPER'),"
            "applications AS (SELECT a.* FROM paper_portfolio_fill_applications a JOIN paper_fills f USING(fill_id)) "
            "SELECT (SELECT count(*) FROM paper_fills),"
            "(SELECT count(*) FROM paper_fills f WHERE EXISTS(SELECT 1 FROM applications a WHERE a.fill_id=f.fill_id)),"
            "(SELECT count(*) FROM applications),"
            "(SELECT count(*) FROM paper_portfolio_pnl_events p JOIN paper_fills f USING(fill_id)),"
            "(SELECT count(*) FROM paper_fills f WHERE NOT EXISTS(SELECT 1 FROM applications a WHERE a.fill_id=f.fill_id)),"
            "(SELECT count(*) FROM applications a WHERE NOT EXISTS(SELECT 1 FROM paper_portfolio_pnl_events p "
            "WHERE p.fill_id=a.fill_id AND p.portfolio_id=a.portfolio_id))"
        )).one()
        return dict(zip(("fills", "applied_fills", "fill_applications", "accounting_receipts", "fills_without_application", "applications_without_pnl"), values))
    def blocked_runs():
        allowed = ",".join(f"'{code}'" for code in BLOCKED_CODES if code != "OTHER")
        rows = dict(connection.execute(text(
            f"SELECT CASE WHEN failure_code IN ({allowed}) THEN failure_code ELSE 'OTHER' END,count(*) "
            "FROM job_runs WHERE left(job_key,6)='paper:' AND status='FAILED' GROUP BY 1"
        )).all())
        return {"total": sum(rows.values()), "by_reason": {key: rows.get(key, 0) for key in BLOCKED_CODES}}
    def decision_activity():
        total, claimed, latest = connection.execute(text(
            "SELECT count(*),count(*) FILTER(WHERE j.status='CLAIMED'),max(n.decision_time) "
            "FROM paper_no_trade_decisions n JOIN job_runs j USING(job_run_id) WHERE left(j.job_key,6)='paper:'"
        )).one()
        return {"no_trade_records": total, "claimed_no_trade": claimed, "latest_decision_at": latest.isoformat() if latest else None}
    def certified_chain():
        total, latest, invalid = connection.execute(text(
            "SELECT count(*),max(a.created_at),count(*) FILTER(WHERE j.job_run_id IS NULL "
            "OR c.classification_id IS NULL OR c.strategy_version_id IS DISTINCT FROM a.strategy_version_id "
            "OR c.research_decision_id IS DISTINCT FROM a.research_decision_id "
            "OR c.state IS DISTINCT FROM 'OPERATIONAL_CANDIDATE' OR NOT (a.market=ANY(c.markets)) "
            "OR a.created_at IS NULL OR a.created_at < j.created_at "
            "OR a.market NOT IN ('INDIA','USA') OR a.database_principal IS NULL OR btrim(a.database_principal)='' "
            "OR split_part(j.job_key,':',1) <> 'paper' OR split_part(j.job_key,':',2) IS DISTINCT FROM a.market "
            "OR split_part(j.job_key,':',3) IS DISTINCT FROM a.strategy_version_id::text) "
            "FROM paper_strategy_authorizations a LEFT JOIN job_runs j USING(job_run_id) "
            "LEFT JOIN strategy_candidate_classifications c USING(classification_id)"
        )).one()
        if invalid or (latest is not None and latest > generated):
            return {"status": "UNAVAILABLE"}
        operational = connection.execute(text(
            "SELECT count(*) FROM current_strategy_candidate_classifications WHERE state='OPERATIONAL_CANDIDATE'"
        )).scalar_one()
        return {"authorization_receipts": total, "latest_authorization_at": latest.isoformat() if latest else None,
                "operational_classifications": operational, "eligibility_policy": "UNKNOWN"}
    def certified_outcomes():
        rows = connection.execute(text(
            "SELECT o.outcome,count(*),count(*) FILTER(WHERE j.status='CLAIMED'),max(o.decision_time),"
            "count(*) FILTER(WHERE j.job_run_id IS NULL OR a.job_run_id IS NULL OR r.research_run_id IS NULL "
            "OR c.classification_id IS NULL OR c.state IS DISTINCT FROM 'OPERATIONAL_CANDIDATE' "
            "OR c.strategy_version_id IS DISTINCT FROM a.strategy_version_id "
            "OR c.research_decision_id IS DISTINCT FROM a.research_decision_id "
            "OR e.strategy_version_id IS DISTINCT FROM a.strategy_version_id "
            "OR rd.decision_id IS NULL OR rd.decision IS DISTINCT FROM 'SUPPORTED' "
            "OR rd.variant_run_id IS DISTINCT FROM o.source_run_id "
            "OR v.dataset_version_id IS NULL OR d.dataset_id IS NULL "
            "OR v.immutable IS DISTINCT FROM TRUE OR d.pit_certified IS DISTINCT FROM TRUE "
            "OR research_d.source IS DISTINCT FROM d.source "
            "OR e.universe_version_id IS DISTINCT FROM o.universe_version_id "
            "OR e.configuration_hash IS DISTINCT FROM o.configuration_hash "
            "OR o.inputs_hash IS NULL OR o.inputs_hash !~ '^[0-9a-f]{64}$' "
            "OR o.reason_code IS NULL OR length(o.reason_code) NOT BETWEEN 1 AND 128 "
            "OR (o.outcome='NO_TRADE' AND o.reason_code<>'NO_SIGNAL') "
            "OR (o.outcome='FILLED' AND o.reason_code<>'PORTFOLIO_RISK_APPROVED') "
            "OR o.decision_time IS NULL OR o.created_at IS NULL OR o.created_at>CURRENT_TIMESTAMP) "
            "FROM paper_certified_outcomes o LEFT JOIN job_runs j USING(job_run_id) "
            "LEFT JOIN paper_strategy_authorizations a USING(job_run_id) "
            "LEFT JOIN research_decisions rd ON rd.decision_id=a.research_decision_id "
            "LEFT JOIN strategy_candidate_classifications c USING(classification_id) "
            "LEFT JOIN research_runs r ON r.research_run_id=o.source_run_id "
            "LEFT JOIN experiments e ON e.experiment_id=r.experiment_id "
            "LEFT JOIN dataset_versions v ON v.dataset_version_id=o.dataset_version_id "
            "LEFT JOIN datasets d ON d.dataset_id=v.dataset_id "
            "LEFT JOIN dataset_versions research_v ON research_v.dataset_version_id=e.dataset_version_id "
            "LEFT JOIN datasets research_d ON research_d.dataset_id=research_v.dataset_id GROUP BY o.outcome"
        )).all()
        if any(outcome not in CERTIFIED_OUTCOMES or invalid or (latest is not None and latest > generated)
               for outcome, _, _, latest, invalid in rows):
            return {"status": "UNAVAILABLE"}
        total = sum(row[1] for row in rows)
        latest = max((row[3] for row in rows if row[3] is not None), default=None)
        counts = {row[0]: row[1] for row in rows}
        allowed = ",".join(f"'{reason}'" for reason in RISK_BLOCK_REASONS if reason != "OTHER")
        reasons = dict(connection.execute(text(
            f"SELECT CASE WHEN reason_code IN ({allowed}) THEN reason_code ELSE 'OTHER' END,count(*) "
            "FROM paper_certified_outcomes WHERE outcome='RISK_BLOCKED' GROUP BY 1"
        )).all())
        return {"total": total, "claimed_records": sum(row[2] for row in rows),
                "latest_decision_at": latest.isoformat() if latest else None,
                "risk_blocked_reasons": {key: reasons.get(key, 0) for key in RISK_BLOCK_REASONS},
                "by_outcome": {key: counts.get(key, 0) for key in CERTIFIED_OUTCOMES}}
    return {"strategy_catalog": strategy_catalog, "dataset_certification": dataset_certification,
            "execution_ledger": execution_ledger, "blocked_runs": blocked_runs,
            "decision_activity": decision_activity, "certified_chain": certified_chain, "certified_outcomes": certified_outcomes}
