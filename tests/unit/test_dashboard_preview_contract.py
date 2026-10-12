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
    assert "PROTOTYPE · NO LIVE DATA" in html
    assert "LIVE broker submission" in html
    assert "NOT ENABLED" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "<form" not in html.lower()


def test_preview_uses_no_external_scripts_or_third_party_assets():
    parser = _DashboardParser()
    parser.feed(PREVIEW.read_text(encoding="utf-8"))
    assert parser.external_scripts == []
    assert "http://" not in PREVIEW.read_text(encoding="utf-8")
    assert "https://" not in PREVIEW.read_text(encoding="utf-8")
