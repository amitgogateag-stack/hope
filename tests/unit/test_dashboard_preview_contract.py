"""Safety and navigability contract for the standalone HOPE dashboard preview."""
from html.parser import HTMLParser
from pathlib import Path

PREVIEW = Path(__file__).resolve().parents[2] / "docs" / "dashboard" / "preview.html"


class _DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.views = set()
        self.tabs = set()
        self.buttons = []
        self.external_scripts = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section" and attrs.get("class", "").find("view") >= 0:
            self.views.add(attrs.get("id"))
        if tag == "button":
            self.buttons.append(attrs)
            if attrs.get("data-view"):
                self.tabs.add(attrs["data-view"])
        if tag == "script" and attrs.get("src"):
            self.external_scripts.append(attrs["src"])


def test_preview_has_all_read_only_sections():
    parser = _DashboardParser()
    parser.feed(PREVIEW.read_text(encoding="utf-8"))
    assert parser.views == {"overview", "research", "paper", "safety"}
    assert parser.tabs == parser.views
    assert all(b.get("type") == "button" and b.get("data-view") for b in parser.buttons)


def test_preview_never_fabricates_runtime_metrics_or_exposes_execution_actions():
    html = PREVIEW.read_text(encoding="utf-8")
    assert "Not connected" in html
    assert "No invented performance metrics" in html
    assert "No trading buttons" in html
    assert "PRIVATE DASHBOARD · VERIFIED DATA ONLY" in html
    assert "LIVE broker submission" in html
    assert "NOT ENABLED" in html
    assert "fetch(" in html
    assert "setInterval(refresh, 60000)" in html
    assert "XMLHttpRequest" not in html
    assert "<form" not in html.lower()


def test_preview_uses_no_external_scripts_or_third_party_assets():
    parser = _DashboardParser()
    parser.feed(PREVIEW.read_text(encoding="utf-8"))
    assert parser.external_scripts == []
    assert "http://" not in PREVIEW.read_text(encoding="utf-8")
    assert "https://" not in PREVIEW.read_text(encoding="utf-8")


def test_preview_imports_local_aggregate_snapshot_without_network_calls():
    html = PREVIEW.read_text(encoding="utf-8")
    assert 'id="snapshot-file"' in html
    assert 'id="snapshot-status"' in html
    assert 'id="research-runs"' in html
    assert 'id="paper-jobs"' in html
    assert 'id="paper-state"' in html
    assert "await file.text()" in html
    assert "Snapshot rejected" in html
    assert "STALE" in html
    assert "fetch(" in html
    assert "setInterval(refresh, 60000)" in html


def test_preview_displays_independent_server_and_database_health():
    html = PREVIEW.read_text(encoding="utf-8")
    for marker in ('id="web-service"', 'id="feed-health"', 'id="feed-time"'):
        assert marker in html
    assert "refreshFeed('/status.json'" in html
    assert "refreshFeed('/snapshot.json'" in html
    assert "CHECK GITHUB ACTIONS" in html
    assert "HOPE CI #1162 PASS" not in html


def test_preview_displays_bounded_transport_observations_without_readiness_claims():
    html = PREVIEW.read_text(encoding="utf-8")
    for marker in ('id="transport-state"', 'id="transport-capacity"',
                   'id="transport-rejected"', 'id="transport-expired"'):
        assert marker in html
    assert "refreshFeed('/transport.json',1024,applyTransport,clearTransport)" in html
    assert "Object.keys(data||{}).sort()" in html
    assert "data.active_clients>data.max_concurrent_clients" in html
    assert "Transport counters do not establish database, PAPER worker, broker or execution readiness" in html



def test_preview_legacy_counts_are_separate_from_paper_and_performance():
    html = PREVIEW.read_text(encoding="utf-8")
    for marker in ('id="legacy-experiments"', 'id="legacy-strategies"',
                   'id="legacy-datasets"', 'id="legacy-status"'):
        assert marker in html
    assert "refreshFeed('/legacy-research.json'" in html
    assert "registrations only · no PAPER or performance claims" in html
    assert "data.paper !== null" in html
    assert "No proven edge asserted" in html
    assert "HOPE CI #1162" not in html
    assert "PASS #1162" not in html


def test_preview_serializes_refreshes_times_out_requests_and_reages_retained_data():
    html = PREVIEW.read_text(encoding="utf-8")
    assert "const activeRefreshes = new Set()" in html
    assert "if (activeRefreshes.has(path)) return" in html
    assert "activeRefreshes.delete(path)" in html
    assert "new AbortController()" in html and "controller.abort()" in html
    assert "signal:controller.signal" in html
    assert "retainSnapshotAfterFailure" in html
    assert "retaining last verified snapshot" in html
    # M1's one-second independent watchdog strengthens the concurrent remote
    # change's 30-second re-aging guarantee, including offline snapshots.
    assert "setInterval(ageViews,1000)" in html
    for path in ("/legacy-research.json", "/status.json", "/snapshot.json"):
        assert f"refreshFeed('{path}'," in html
