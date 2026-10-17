"""Bounded, fail-closed loopback acceptance probe for pinned dashboard upgrades.

Only an unavailable socket is retried: malformed data, redirects, wrong release,
wrong origin or invalid HTTP responses fail immediately.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from hope.api.dashboard_projection import verified_projections
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_MAX_BYTES = 32768
_MAX_PAGE_BYTES = 65536
_REQUIRED_MARKERS = (
    b'id="legacy-experiments"',
    b'id="legacy-strategies"',
    b'id="legacy-datasets"',
    b"HOPE dashboard v0.5",
)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read(opener, url: str, limit: int) -> tuple[int, bytes]:
    request = Request(url, headers={"Host": urlsplit(url).netloc})
    try:
        with opener.open(request, timeout=3) as response:
            status, body = response.status, response.read(limit + 1)
    except HTTPError as exc:
        with exc:
            status, body = exc.code, exc.read(limit + 1)
    if len(body) > limit:
        raise ValueError("HOPE_UPGRADE_OVERSIZED_HTTP_RESPONSE")
    return status, body


def _json(body: bytes) -> dict:
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("HOPE_UPGRADE_INVALID_JSON")
    return data



_MAX_SAFE_INTEGER = 9007199254740991


def _exact_keys(value, fields: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == fields


def _count(value) -> bool:
    return type(value) is int and 0 <= value <= _MAX_SAFE_INTEGER


def _timestamp(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (parsed.tzinfo is not None
            and (parsed - datetime.now(timezone.utc)).total_seconds() <= 300)


def _validate_legacy(value: dict) -> None:
    fields = {"registered_experiments", "registered_strategy_versions", "registered_datasets"}
    if (not _exact_keys(value, {"schema_version", "source", "generated_at",
                                "schema_compatible", "research", "paper"})
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["source"] != "hope_legacy_research_inventory"
            or value["schema_compatible"] is not True or value["paper"] is not None
            or not _timestamp(value["generated_at"])
            or not _exact_keys(value["research"], fields)
            or not all(_count(value["research"][key]) for key in fields)):
        raise ValueError("HOPE_UPGRADE_LEGACY_INVALID")


def _validate_paper(value: dict) -> None:
    fields = {"total", "claimed", "succeeded", "failed"}
    paper = value.get("paper")
    paper_keys = {"environment_state", "job_runs"}
    # Recovery aggregates are a backward-compatible extension, but their exact
    # response shape remains part of the no-private-fields acceptance boundary.
    valid_paper_keys = (isinstance(paper, dict)
                        and set(paper) in (paper_keys, paper_keys | {"recovery"}))
    outer = {"schema_version", "source", "generated_at", "research", "paper"}
    if (set(value) not in (outer, outer | {"projections"})
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["source"] != "hope_postgresql"
            or not _timestamp(value["generated_at"])
            or not _exact_keys(value["research"], {"run_count"})
            or not _count(value["research"]["run_count"])
            or not valid_paper_keys
            or value["paper"]["environment_state"] not in ("RUNNING", "HALTED")
            or not _exact_keys(value["paper"]["job_runs"], fields)
            or not all(_count(value["paper"]["job_runs"][key]) for key in fields)):
        raise ValueError("HOPE_UPGRADE_PAPER_INVALID")
    counts = value["paper"]["job_runs"]
    if counts["total"] != sum(counts[k] for k in ("claimed", "succeeded", "failed")):
        raise ValueError("HOPE_UPGRADE_PAPER_INCONSISTENT")
    if "recovery" in paper:
        recovery = paper["recovery"]
        recovery_fields = {
            "claimed_with_effects", "claimed_without_effects",
            "claims_older_than_one_hour", "reconciliation_receipts",
        }
        if (not _exact_keys(recovery, recovery_fields)
                or not all(_count(recovery[key]) for key in recovery_fields)):
            raise ValueError("HOPE_UPGRADE_PAPER_RECOVERY_INVALID")
        if (recovery["claimed_with_effects"] + recovery["claimed_without_effects"] != counts["claimed"]
                or recovery["claims_older_than_one_hour"] > counts["claimed"]
                or recovery["reconciliation_receipts"] > counts["succeeded"]):
            raise ValueError("HOPE_UPGRADE_PAPER_RECOVERY_INCONSISTENT")
    if "projections" in value:
        try:
            generated = datetime.fromisoformat(value["generated_at"].replace("Z", "+00:00"))
            if verified_projections(value["projections"], generated) != value["projections"]:
                raise ValueError("unsanitized projection")
        except (TypeError, ValueError) as error:
            raise ValueError("HOPE_UPGRADE_PAPER_PROJECTIONS_INVALID") from error


def verify_dashboard(base: str = "http://127.0.0.1:8787", *, opener=None,
                     activation_started: datetime | None = None,
                     activation_snapshot: bytes | None = None) -> dict[str, str]:
    expected = None
    if activation_started is not None or activation_snapshot is not None:
        if (not isinstance(activation_started, datetime) or activation_started.tzinfo is None
                or activation_started.utcoffset() is None or not isinstance(activation_snapshot, bytes)
                or len(activation_snapshot) > _MAX_BYTES):
            raise ValueError("HOPE_UPGRADE_ACTIVATION_INPUT_INVALID")
        expected = _json(activation_snapshot)
        _validate_paper(expected)
        generated = datetime.fromisoformat(expected["generated_at"].replace("Z", "+00:00"))
        if not activation_started <= generated <= datetime.now(timezone.utc):
            raise ValueError("HOPE_UPGRADE_ACTIVATION_SNAPSHOT_NOT_NEW")
    parts = urlsplit(base)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise ValueError("HOPE_UPGRADE_LOOPBACK_ONLY")
    opener = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirect())
    status_code, body = _read(opener, base + "/status.json", 4096)
    if status_code != 200:
        raise ValueError("HOPE_UPGRADE_HEALTH_HTTP_INVALID")
    status = _json(body)
    if (not _exact_keys(status, {"schema_version", "service", "data_feed", "snapshot_generated_at"})
            or type(status["schema_version"]) is not int or status["schema_version"] != 1
            or status["service"] != "RUNNING"
            or status["data_feed"] not in ("CURRENT", "STALE", "UNAVAILABLE")
            or (status["data_feed"] == "UNAVAILABLE" and status["snapshot_generated_at"] is not None)
            or (status["data_feed"] != "UNAVAILABLE" and not _timestamp(status["snapshot_generated_at"]))):
        raise ValueError("HOPE_UPGRADE_HEALTH_INVALID")
    page_code, page = _read(opener, base + "/", _MAX_PAGE_BYTES)
    if page_code != 200 or any(marker not in page for marker in _REQUIRED_MARKERS):
        raise ValueError("HOPE_UPGRADE_RELEASE_MISMATCH")
    legacy_code, legacy_body = _read(opener, base + "/legacy-research.json", 8192)
    if legacy_code == 200:
        legacy = _json(legacy_body)
        _validate_legacy(legacy)
    elif legacy_code != 503 or legacy_body != b"Verified legacy research inventory unavailable":
        raise ValueError("HOPE_UPGRADE_LEGACY_HTTP_INVALID")
    paper_code, paper_body = _read(opener, base + "/snapshot.json", _MAX_BYTES)
    if paper_code == 200:
        paper = _json(paper_body)
        _validate_paper(paper)
    elif paper_code != 503 or paper_body != b"Verified snapshot unavailable":
        raise ValueError("HOPE_UPGRADE_PAPER_HTTP_INVALID")
    if expected is not None:
        # Activation must prove the exact selected post-switch export is the
        # snapshot presented over loopback. Ordinary upgrade observation keeps
        # its existing independent/retained-feed semantics.
        if (paper_code != 200 or status["data_feed"] != "CURRENT" or paper != expected
                or datetime.fromisoformat(status["snapshot_generated_at"].replace("Z", "+00:00")) != generated):
            raise ValueError("HOPE_UPGRADE_ACTIVATION_PRESENTATION_MISMATCH")
    return {
        "service": "RUNNING",
        "paper_feed": "AVAILABLE" if paper_code == 200 else "UNAVAILABLE",
        "legacy_feed": "AVAILABLE" if legacy_code == 200 else "UNAVAILABLE",
    }


def verify_with_startup_retry(*, attempts: int = 5, delay: float = 1.0, opener=None) -> dict[str, str]:
    if not 1 <= attempts <= 10 or not 0 <= delay <= 5:
        raise ValueError("HOPE_UPGRADE_RETRY_POLICY_INVALID")
    for attempt in range(attempts):
        try:
            return verify_dashboard(opener=opener)
        except (URLError, TimeoutError, ConnectionError):
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
    raise AssertionError("unreachable")


def verify_readiness(base: str = "http://127.0.0.1:8788", *, opener=None) -> dict:
    """Verify the bounded M3 readiness response without treating it as authorization."""
    parts = urlsplit(base)
    if (parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.username
            or parts.password or parts.path or parts.query or parts.fragment):
        raise ValueError("HOPE_UPGRADE_LOOPBACK_ONLY")
    opener = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirect())
    code, body = _read(opener, base + "/readiness.json", 8192)
    if code != 200:
        raise ValueError("HOPE_READINESS_HTTP_INVALID")
    value = _json(body)
    fields = {
        "schema", "generated_at", "server", "paper_health", "reason", "worker_report",
        "snapshot", "database", "reconciliation", "last_successful_poll_at",
        "last_successful_paper_at", "last_failure_at", "last_failure_code",
        "execution_authorized", "paper_only", "live_enabled",
    }
    timestamps = ("last_successful_poll_at", "last_successful_paper_at", "last_failure_at")
    if (not _exact_keys(value, fields) or value["schema"] != "hope.operational-readiness.v1"
            or not _timestamp(value["generated_at"]) or value["server"] != "RUNNING"
            or value["paper_health"] not in {"UNAVAILABLE", "HALTED", "READY"}
            or value["snapshot"] not in {"UNAVAILABLE", "STALE", "CURRENT"}
            or value["database"] not in {"UNVERIFIED", "OBSERVED_AT_SNAPSHOT"}
            or value["reconciliation"] not in {"UNVERIFIED", "CLAIMS_REQUIRE_REVIEW"}
            or value["execution_authorized"] is not False or value["paper_only"] is not True
            or value["live_enabled"] is not False
            or any(value[name] is not None and not _timestamp(value[name]) for name in timestamps)
            or (value["last_failure_at"] is None) != (value["last_failure_code"] is None)
            or not isinstance(value["reason"], str) or not isinstance(value["worker_report"], str)):
        raise ValueError("HOPE_READINESS_RESPONSE_INVALID")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail-closed private dashboard upgrade acceptance")
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()
    result = verify_with_startup_retry(attempts=args.attempts, delay=args.delay)
    print("Verified private dashboard:", result["service"],
          "PAPER", result["paper_feed"], "legacy", result["legacy_feed"])


if __name__ == "__main__":
    main()
