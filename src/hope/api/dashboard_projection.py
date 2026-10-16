"""Fixed aggregate-only optional dashboard projection contract."""
from datetime import datetime, timezone

EXPERIMENT_STATES = ("CREATED", "RUNNING", "COMPLETED", "FAILED", "INVALIDATED", "OTHER")
VERDICTS = ("SUPPORTED", "WEAK_EVIDENCE", "INCONCLUSIVE", "REJECTED", "INVALIDATED", "REQUIRES_MORE_DATA")
SECTIONS = ("datasets", "strategies", "experiments", "decisions", "paper_activity", "market_data", "portfolio")


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
    for section in SECTIONS:
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
        else:
            raise ValueError("HOPE_DASHBOARD_PORTFOLIO_UNVERIFIED")
        safe[section] = result
    return safe
