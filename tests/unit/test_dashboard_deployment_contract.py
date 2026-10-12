"""The EC2 dashboard remains a separate, least-privilege loopback service."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "deploy" / "dashboard"


def test_systemd_units_are_non_root_and_separate():
    export = (ROOT / "hope-dashboard-export.service").read_text()
    web = (ROOT / "hope-dashboard-web.service").read_text()
    timer = (ROOT / "hope-dashboard-export.timer").read_text()
    for unit in (export, web):
        assert "User=hope-dashboard" in unit
        assert "NoNewPrivileges=yes" in unit
        assert "ProtectSystem=strict" in unit
        assert "WorkingDirectory=/opt/hope" in unit
    assert "EnvironmentFile=/etc/hope-dashboard.env" in export
    assert "EnvironmentFile=" not in web
    assert "ReadWritePaths=/var/lib/hope-dashboard" in export
    assert "ReadWritePaths=" not in web
    assert "OnUnitActiveSec=5min" in timer


def test_deployment_has_no_public_port_or_trading_controls():
    files = [p.read_text() for p in ROOT.glob("*.service")]
    assert len(files) == 2
    joined = "\n".join(files)
    assert "--port 8787" in joined
    assert "hope.api.dashboard_server" in joined
    assert "hope.api.dashboard_snapshot" in joined
    assert "0.0.0.0" not in joined
    assert "LIVE" not in joined
    assert "broker" not in joined.lower()
