"""Bounded, fail-closed loopback acceptance probe for pinned dashboard upgrades.

Only an unavailable socket is retried: malformed data, redirects, wrong release,
wrong origin or invalid HTTP responses fail immediately.
"""
from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_MAX_BYTES = 32768
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


def verify_dashboard(base: str = "http://127.0.0.1:8787", *, opener=None) -> dict[str, str]:
    parts = urlsplit(base)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise ValueError("HOPE_UPGRADE_LOOPBACK_ONLY")
    opener = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirect())
    status_code, body = _read(opener, base + "/status.json", 4096)
    if status_code != 200:
        raise ValueError("HOPE_UPGRADE_HEALTH_HTTP_INVALID")
    status = _json(body)
    if (type(status.get("schema_version")) is not int or status["schema_version"] != 1
            or status.get("service") != "RUNNING"
            or status.get("data_feed") not in ("CURRENT", "STALE", "UNAVAILABLE")):
        raise ValueError("HOPE_UPGRADE_HEALTH_INVALID")
    page_code, page = _read(opener, base + "/", _MAX_BYTES)
    if page_code != 200 or any(marker not in page for marker in _REQUIRED_MARKERS):
        raise ValueError("HOPE_UPGRADE_RELEASE_MISMATCH")
    legacy_code, legacy_body = _read(opener, base + "/legacy-research.json", 8192)
    if legacy_code == 200:
        legacy = _json(legacy_body)
        if (legacy.get("schema_version") != 1
                or legacy.get("source") != "hope_legacy_research_inventory"
                or legacy.get("paper") is not None
                or legacy.get("schema_compatible") is not True):
            raise ValueError("HOPE_UPGRADE_LEGACY_INVALID")
    elif legacy_code != 503 or legacy_body != b"Verified legacy research inventory unavailable":
        raise ValueError("HOPE_UPGRADE_LEGACY_HTTP_INVALID")
    paper_code, paper_body = _read(opener, base + "/snapshot.json", _MAX_BYTES)
    if paper_code == 200:
        paper = _json(paper_body)
        if (paper.get("schema_version") != 1
                or paper.get("source") != "hope_postgresql"
                or not isinstance(paper.get("paper"), dict)):
            raise ValueError("HOPE_UPGRADE_PAPER_INVALID")
    elif paper_code != 503 or paper_body != b"Verified snapshot unavailable":
        raise ValueError("HOPE_UPGRADE_PAPER_HTTP_INVALID")
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
