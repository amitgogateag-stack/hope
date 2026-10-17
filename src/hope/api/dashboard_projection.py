"""Fixed aggregate-only optional dashboard projection contract."""
from datetime import datetime, timezone
from uuid import UUID

EXPERIMENT_STATES = ("CREATED", "RUNNING", "COMPLETED", "FAILED", "INVALIDATED", "OTHER")
VERDICTS = ("SUPPORTED", "WEAK_EVIDENCE", "INCONCLUSIVE", "REJECTED", "INVALIDATED", "REQUIRES_MORE_DATA")
SECTIONS = ("datasets", "strategies", "experiments", "decisions", "paper_activity", "market_data", "portfolio")
M2_SECTIONS = ("strategy_catalog", "dataset_certification", "execution_ledger", "blocked_runs", "decision_activity", "certified_chain", "certified_outcomes")
CERTIFIED_OUTCOMES = ("NO_TRADE", "RISK_BLOCKED", "FILLED")
RISK_BLOCK_REASONS = ("MAX_DAILY_LOSS_REACHED", "MAX_DRAWDOWN_REACHED", "MAX_POSITION_NOTIONAL_EXCEEDED", "MAX_STRATEGY_EXPOSURE_EXCEEDED", "CONCENTRATION_CONTEXT_REQUIRED", "MAX_CONCENTRATION_EXPOSURE_EXCEEDED", "MAX_GROSS_EXPOSURE_EXCEEDED", "MAX_OPEN_POSITIONS_EXCEEDED", "INTELLIGENCE_ENTRY_REVIEW_REQUIRED", "OTHER")
FAMILY_CODES = ("CROSS_SECTIONAL_MOMENTUM", "FIFTY_TWO_WEEK_HIGH", "TIME_SERIES_TREND", "LOW_VOLATILITY_DEFENSIVE", "QUALITY_DEFENSIVE", "MULTIFACTOR_QMV", "SECTOR_INDUSTRY_MOMENTUM", "SHORT_TERM_REVERSAL", "EARNINGS_DRIFT", "VALUE_RECOVERY", "OTHER")
CANDIDATE_STATES = ("RESEARCH", "BACKUP_CANDIDATE", "OPERATIONAL_CANDIDATE", "UNCLASSIFIED")
BLOCKED_CODES = ("PAPER_JOB_INCOMPLETE_PRIOR_CLAIM", "PAPER_MARKET_DATA_STALE", "PAPER_MARKET_DATA_MISSING_ACTIVE_INSTRUMENT", "OTHER")


def verified_projections(value: object, generated: datetime) -> dict:
    if not isinstance(value, dict):
        raise ValueError("HOPE_DASHBOARD_PROJECTIONS_INVALID")
    def count(number):
        if type(number) is not int or not 0 <= number <= 9007199254740991:
            raise ValueError("HOPE_DASHBOARD_PROJECTION_COUNT_INVALID")
        return number
    def timestamp(raw):
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise ValueError("HOPE_DASHBOARD_PROJECTION_TIME_INVALID")
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed > generated:
            raise ValueError("HOPE_DASHBOARD_PROJECTION_TIME_INVALID")
        return parsed.astimezone(timezone.utc).isoformat()
    safe = {}
    for section in SECTIONS + tuple(key for key in M2_SECTIONS if key in value):
        item = value.get(section, {"status": "UNAVAILABLE"})
        if not isinstance(item, dict) or item.get("status") not in {"AVAILABLE", "UNAVAILABLE"}:
            raise ValueError("HOPE_DASHBOARD_PROJECTION_STATUS_INVALID")
        if item["status"] == "UNAVAILABLE":
            safe[section] = {"status": "UNAVAILABLE"}
            continue
        result = {"status": "AVAILABLE"}
        if section == "datasets":
            result.update(registered=count(item.get("registered")), pit_certified=count(item.get("pit_certified")))
            if result["pit_certified"] > result["registered"]:
                raise ValueError("HOPE_DASHBOARD_PROJECTION_COUNT_INCONSISTENT")
        elif section == "strategies":
            result["versions"] = count(item.get("versions"))
        elif section in {"experiments", "decisions"}:
            fields = EXPERIMENT_STATES if section == "experiments" else VERDICTS
            name = "by_status" if section == "experiments" else "by_verdict"
            buckets = item.get(name)
            if not isinstance(buckets, dict):
                raise ValueError("HOPE_DASHBOARD_PROJECTION_BUCKETS_INVALID")
            result[name] = {key: count(buckets.get(key)) for key in fields}
            result["total"] = count(item.get("total"))
            if result["total"] != sum(result[name].values()):
                raise ValueError("HOPE_DASHBOARD_PROJECTION_COUNT_INCONSISTENT")
        elif section == "paper_activity":
            result.update(last_completed_at=timestamp(item.get("last_completed_at")),
                          succeeded_no_trade=count(item.get("succeeded_no_trade")))
        elif section == "market_data":
            if item.get("freshness_policy") != "UNKNOWN":
                raise ValueError("HOPE_DASHBOARD_PROJECTION_POLICY_UNVERIFIED")
            result.update(bar_count=count(item.get("bar_count")),
                          latest_event_at=timestamp(item.get("latest_event_at")),
                          latest_available_at=timestamp(item.get("latest_available_at")), freshness_policy="UNKNOWN")
            if ((result["bar_count"] == 0) != (result["latest_event_at"] is None and result["latest_available_at"] is None)
                    or (result["bar_count"] > 0 and (result["latest_event_at"] is None or result["latest_available_at"] is None))):
                raise ValueError("HOPE_DASHBOARD_PROJECTION_TIME_INCONSISTENT")
            if result["latest_event_at"] is not None and result["latest_event_at"] > result["latest_available_at"]:
                raise ValueError("HOPE_DASHBOARD_PROJECTION_TIME_INCONSISTENT")
        elif section == "strategy_catalog":
            rows = item.get("rows")
            result["total"] = count(item.get("total"))
            if not isinstance(rows, list) or len(rows) > 20 or type(item.get("truncated")) is not bool:
                raise ValueError("HOPE_DASHBOARD_CATALOG_INVALID")
            safe_rows = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("HOPE_DASHBOARD_CATALOG_INVALID")
                raw_id = row.get("strategy_version_id")
                if not isinstance(raw_id, str) or str(UUID(raw_id)) != raw_id:
                    raise ValueError("HOPE_DASHBOARD_CATALOG_ID_INVALID")
                if row.get("family_code") not in FAMILY_CODES or row.get("candidate_state") not in CANDIDATE_STATES:
                    raise ValueError("HOPE_DASHBOARD_CATALOG_ENUM_INVALID")
                markets = row.get("markets")
                if not isinstance(markets, list) or any(not isinstance(m, str) or m not in ("INDIA", "USA") for m in markets) or markets != sorted(set(markets)):
                    raise ValueError("HOPE_DASHBOARD_CATALOG_MARKETS_INVALID")
                safe_rows.append({"strategy_version_id": raw_id, "family_code": row["family_code"], "candidate_state": row["candidate_state"], "markets": markets})
            ids = [row["strategy_version_id"] for row in safe_rows]
            if ids != sorted(set(ids)) or len(rows) != min(result["total"], 20) or item["truncated"] != (result["total"] > 20):
                raise ValueError("HOPE_DASHBOARD_CATALOG_COUNT_INCONSISTENT")
            result.update(rows=safe_rows, truncated=item["truncated"])
        elif section == "dataset_certification":
            fields = ("version_total", "sealed_versions", "pit_certified_versions", "manifest_backed_sealed_versions")
            result.update({key: count(item.get(key)) for key in fields})
            if (result["sealed_versions"] > result["version_total"] or result["pit_certified_versions"] > result["sealed_versions"]
                    or result["manifest_backed_sealed_versions"] > result["sealed_versions"]):
                raise ValueError("HOPE_DASHBOARD_CERTIFICATION_COUNT_INCONSISTENT")
        elif section == "execution_ledger":
            fields = ("fills", "applied_fills", "fill_applications", "accounting_receipts", "fills_without_application", "applications_without_pnl")
            result.update({key: count(item.get(key)) for key in fields})
            if (result["fills"] != result["applied_fills"] + result["fills_without_application"]
                    or result["applied_fills"] > result["fill_applications"] or result["applications_without_pnl"] > result["fill_applications"]):
                raise ValueError("HOPE_DASHBOARD_LEDGER_COUNT_INCONSISTENT")
        elif section == "blocked_runs":
            buckets = item.get("by_reason")
            if not isinstance(buckets, dict):
                raise ValueError("HOPE_DASHBOARD_BLOCKED_REASONS_INVALID")
            result.update(total=count(item.get("total")), by_reason={key: count(buckets.get(key)) for key in BLOCKED_CODES})
            if result["total"] != sum(result["by_reason"].values()):
                raise ValueError("HOPE_DASHBOARD_BLOCKED_COUNT_INCONSISTENT")
        elif section == "decision_activity":
            result.update(no_trade_records=count(item.get("no_trade_records")), claimed_no_trade=count(item.get("claimed_no_trade")), latest_decision_at=timestamp(item.get("latest_decision_at")))
            if result["claimed_no_trade"] > result["no_trade_records"] or (result["no_trade_records"] == 0) != (result["latest_decision_at"] is None):
                raise ValueError("HOPE_DASHBOARD_DECISION_COUNT_INCONSISTENT")
        elif section == "certified_chain":
            result.update(authorization_receipts=count(item.get("authorization_receipts")), operational_classifications=count(item.get("operational_classifications")), latest_authorization_at=timestamp(item.get("latest_authorization_at")))
            if item.get("eligibility_policy") != "UNKNOWN" or (result["authorization_receipts"] == 0) != (result["latest_authorization_at"] is None):
                raise ValueError("HOPE_DASHBOARD_CHAIN_PROOF_UNVERIFIED")
            result["eligibility_policy"] = "UNKNOWN"
        elif section == "certified_outcomes":
            buckets = item.get("by_outcome")
            reasons = item.get("risk_blocked_reasons")
            if not isinstance(buckets, dict) or not isinstance(reasons, dict):
                raise ValueError("HOPE_DASHBOARD_CERTIFIED_OUTCOME_INVALID")
            result.update(total=count(item.get("total")), claimed_records=count(item.get("claimed_records")),
                          latest_decision_at=timestamp(item.get("latest_decision_at")),
                          by_outcome={key: count(buckets.get(key)) for key in CERTIFIED_OUTCOMES})
            result["risk_blocked_reasons"] = {key: count(reasons.get(key)) for key in RISK_BLOCK_REASONS}
            if (result["total"] != sum(result["by_outcome"].values()) or result["claimed_records"] > result["total"]
                    or (result["total"] == 0) != (result["latest_decision_at"] is None)
                    or sum(result["risk_blocked_reasons"].values()) != result["by_outcome"]["RISK_BLOCKED"]):
                raise ValueError("HOPE_DASHBOARD_CERTIFIED_OUTCOME_INCONSISTENT")
        else:
            raise ValueError("HOPE_DASHBOARD_PORTFOLIO_UNVERIFIED")
        safe[section] = result
    return safe
