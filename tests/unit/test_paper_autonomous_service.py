from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import json
import os
import socket
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from hope.api.paper_service import load_plan_factory, main
from hope.infrastructure import paper_service as service
from hope.infrastructure.scheduling.paper import OperationalPaperJobBinding, OperationalPaperSchedule
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.domain.strategy.candidates import StrategyMarket

NOW = datetime(2025, 9, 15, 14, tzinfo=UTC)


def plan(**changes):
    version = uuid4()
    values = dict(bindings=(OperationalPaperJobBinding(version, StrategyMarket.USA, "test", lambda runtime: None),),
        schedules=(OperationalPaperSchedule(version, StrategyMarket.USA, "test", timedelta(0)),),
        calendars={StrategyMarket.USA: MarketSessionCalendar(((NOW, NOW + timedelta(hours=1)),))},
        start=NOW, end=NOW + timedelta(hours=1), max_lateness=timedelta(minutes=1))
    values.update(changes)
    return service.PaperServicePlan(**values)


@pytest.mark.parametrize("changes", [dict(bindings=()), dict(bindings=[]), dict(start=NOW.replace(tzinfo=None)),
    dict(end=NOW), dict(end=NOW + timedelta(days=8)), dict(max_lateness=timedelta(seconds=-1)),
    dict(calendars={"USA": MarketSessionCalendar(())}), dict(bindings=(object(),)), dict(schedules=(object(),))])
def test_plan_rejects_unbounded_or_ambiguous_configuration(changes):
    with pytest.raises((ValueError, TypeError)):
        plan(**changes)


def test_plan_freezes_reviewed_calendars_against_caller_mutation():
    original = MarketSessionCalendar(((NOW, NOW + timedelta(hours=1)),))
    replacement = MarketSessionCalendar(((NOW + timedelta(days=1), NOW + timedelta(days=1, hours=1)),))
    calendars = {StrategyMarket.USA: original}
    approved = plan(calendars=calendars)

    calendars[StrategyMarket.USA] = replacement

    assert approved.calendars == {StrategyMarket.USA: original}
    with pytest.raises(TypeError):
        approved.calendars[StrategyMarket.USA] = replacement


def test_plan_reads_each_reviewed_calendar_once():
    original = MarketSessionCalendar(((NOW, NOW + timedelta(hours=1)),))
    replacement = MarketSessionCalendar(((NOW + timedelta(days=1), NOW + timedelta(days=1, hours=1)),))

    class DriftingCalendars(Mapping):
        reads = 0

        def __iter__(self):
            return iter((StrategyMarket.USA,))

        def __len__(self):
            return 1

        def __getitem__(self, key):
            assert key is StrategyMarket.USA
            self.reads += 1
            return original if self.reads == 1 else replacement

    calendars = DriftingCalendars()
    approved = plan(calendars=calendars)
    assert approved.calendars == {StrategyMarket.USA: original}
    assert calendars.reads == 1


def test_status_replacement_is_atomic_and_private(tmp_path, monkeypatch):
    target = tmp_path / "health.json"
    service.publish_service_status(target, {"state": "CURRENT"})
    original = target.read_bytes()
    def failed_replace(*args):
        raise OSError("INTERRUPTED")
    monkeypatch.setattr(service, "durable_replace", failed_replace)
    with pytest.raises(OSError):
        service.publish_service_status(target, {"state": "HALTED"})
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("exception,code", [
    (RuntimeError("PAPER_ENVIRONMENT_HALTED"), "PAPER_ENVIRONMENT_HALTED"),
    (RuntimeError("PAPER_MARKET_DATA_STALE"), "PAPER_MARKET_DATA_STALE"),
    (RuntimeError("password=secret"), "PAPER_SERVICE_CYCLE_FAILED"),
    (OperationalError("password=secret", {}, Exception("token")), "PAPER_SERVICE_DATABASE_UNAVAILABLE")])
def test_once_health_redacts_failures(tmp_path, monkeypatch, exception, code):
    def fail(*args, **kwargs):
        raise exception
    monkeypatch.setattr(service, "run_service_cycle", fail)
    disposed = []
    engine = SimpleNamespace(dispose=lambda: disposed.append(True))
    target = tmp_path / "health.json"
    assert service.run_service(engine, lambda *_: None, migrations_dir=tmp_path, status_file=target, once=True) == 1
    value = json.loads(target.read_text())
    assert value["error_code"] == code
    assert value["cycles_failed"] == 1
    assert value["last_success_at"] is None
    assert "secret" not in target.read_text() and "token" not in target.read_text()
    assert bool(disposed) is isinstance(exception, OperationalError)


def test_successful_once_publishes_terminal_stopped_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(service, "run_service_cycle", lambda *args, **kwargs: (1, 2))
    target = tmp_path / "health.json"
    notifications = []
    notifier = SimpleNamespace(
        ready=lambda: notifications.append("ready"),
        watchdog=lambda: notifications.append("watchdog"),
        stopping=lambda: notifications.append("stopping"),
    )
    assert service.run_service(
        SimpleNamespace(),
        lambda *_: None,
        migrations_dir=tmp_path,
        status_file=target,
        once=True,
        notifier=notifier,
    ) == 0
    value = json.loads(target.read_text())
    assert value["state"] == "STOPPED"
    assert value["last_success_at"] is not None
    assert value["schema_compatibility"] == "COMPATIBLE"
    assert (value["cycles_succeeded"], value["cycles_failed"]) == (1, 0)
    assert (value["jobs_executed"], value["jobs_skipped"]) == (1, 2)
    publications = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["state"] for item in publications] == ["STARTING", "CURRENT", "STOPPED"]
    assert publications[-1] == value
    assert notifications == ["ready", "watchdog", "stopping"]


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Requires Unix notification sockets")
def test_systemd_notifier_uses_main_process_watchdog_protocol(tmp_path, monkeypatch):
    path = tmp_path / "notify.sock"
    try:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    except PermissionError:
        pytest.skip("Managed runner blocks native Unix sockets; exercised on Linux CI")
    with server:
        server.bind(str(path))
        server.settimeout(2)
        monkeypatch.setenv("NOTIFY_SOCKET", str(path))
        monkeypatch.setenv("WATCHDOG_PID", str(os.getpid()))
        monkeypatch.setenv("WATCHDOG_USEC", "900000000")
        notifier = service.SystemdNotifier.from_environment()
        notifier.ready()
        notifier.watchdog()
        notifier.stopping()
        messages = [server.recv(512).decode("utf-8") for _ in range(3)]
    assert messages[0].startswith("READY=1\nWATCHDOG=1\n")
    assert messages[1].startswith("WATCHDOG=1\n")
    assert messages[2].startswith("STOPPING=1\n")
    assert all("secret" not in message for message in messages)


@pytest.mark.parametrize("environment", [
    {"NOTIFY_SOCKET": "relative", "WATCHDOG_USEC": "900000000"},
    {"NOTIFY_SOCKET": "/run/systemd/notify", "WATCHDOG_USEC": "invalid"},
    {"NOTIFY_SOCKET": "/run/systemd/notify", "WATCHDOG_PID": "invalid", "WATCHDOG_USEC": "900000000"},
])
def test_invalid_systemd_notification_environment_fails_closed(monkeypatch, environment):
    for name in ("NOTIFY_SOCKET", "WATCHDOG_PID", "WATCHDOG_USEC"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="NOTIFY_CONFIGURATION_INVALID"):
        service.SystemdNotifier.from_environment()


def test_watchdog_for_another_process_is_ignored(monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "/run/systemd/notify")
    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid() + 1))
    monkeypatch.setenv("WATCHDOG_USEC", "900000000")
    assert service.SystemdNotifier.from_environment().address is None


def test_notifier_sends_only_fixed_protocol_without_environment_payload(monkeypatch):
    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def settimeout(self, seconds):
            assert seconds == 1.0

        def connect(self, address):
            calls.append(("connect", address))

        def sendall(self, payload):
            calls.append(("send", payload))

    unix_family = object()
    monkeypatch.setattr(service.socket, "AF_UNIX", unix_family, raising=False)
    monkeypatch.setattr(
        service.socket,
        "socket",
        lambda family, kind: Client()
        if (family, kind) == (unix_family, socket.SOCK_DGRAM)
        else pytest.fail("unexpected notification socket protocol"),
    )
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-send")
    notifier = service.SystemdNotifier("/run/systemd/notify")
    notifier.ready()
    notifier.watchdog()
    notifier.stopping()
    payloads = [value.decode("utf-8") for operation, value in calls if operation == "send"]
    assert len(payloads) == 3
    assert payloads[0].startswith("READY=1\nWATCHDOG=1\n")
    assert payloads[1].startswith("WATCHDOG=1\n")
    assert payloads[2].startswith("STOPPING=1\n")
    assert "do-not-send" not in "".join(payloads)


def test_notifier_without_unix_socket_support_fails_closed(monkeypatch):
    monkeypatch.delattr(service.socket, "AF_UNIX", raising=False)
    monkeypatch.setattr(
        service.socket,
        "socket",
        lambda *_: pytest.fail("socket must not be opened without AF_UNIX support"),
    )
    with pytest.raises(RuntimeError, match="^PAPER_SERVICE_NOTIFY_FAILED$"):
        service.SystemdNotifier("/run/systemd/notify").watchdog()


@pytest.mark.parametrize("failure_point", ["open", "connect", "send"])
def test_notifier_io_failures_expose_only_fixed_error(monkeypatch, failure_point):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def settimeout(self, seconds):
            assert seconds == 1.0

        def connect(self, _address):
            if failure_point == "connect":
                raise OSError("credential-shaped transport detail")

        def sendall(self, _payload):
            if failure_point == "send":
                raise OSError("credential-shaped transport detail")

    def open_socket(*_):
        if failure_point == "open":
            raise OSError("credential-shaped transport detail")
        return Client()

    monkeypatch.setattr(service.socket, "AF_UNIX", object(), raising=False)
    monkeypatch.setattr(service.socket, "socket", open_socket)
    with pytest.raises(RuntimeError, match="^PAPER_SERVICE_NOTIFY_FAILED$") as raised:
        service.SystemdNotifier("/run/systemd/notify").ready()
    assert "credential-shaped" not in str(raised.value)


def test_service_recovers_on_next_poll_and_stops_gracefully(tmp_path, monkeypatch):
    stop = Event()
    calls = []
    def cycle(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("PAPER_SERVICE_SCHEMA_INCOMPATIBLE")
        stop.set()
        return 1, 2
    monkeypatch.setattr(service, "run_service_cycle", cycle)
    target = tmp_path / "health.json"
    assert service.run_service(SimpleNamespace(), lambda *_: None, migrations_dir=tmp_path,
        status_file=target, stop_event=stop, poll_seconds=1) == 0
    value = json.loads(target.read_text())
    assert value["state"] == "STOPPED" and value["last_success_at"] is not None
    assert (value["cycles_succeeded"], value["cycles_failed"], value["jobs_executed"], value["jobs_skipped"]) == (1, 1, 1, 2)


@pytest.mark.parametrize("seconds", [0, 301, True, float("nan"), "30"])
def test_invalid_poll_interval_fails_before_publication(tmp_path, seconds):
    with pytest.raises(ValueError):
        service.run_service(None, None, migrations_dir=tmp_path, status_file=tmp_path / "health", poll_seconds=seconds)
    assert not (tmp_path / "health").exists()


@pytest.mark.parametrize("spec", ["bad", "x:y:z", "bad-name:func", "x:bad-name"])
def test_factory_import_spec_is_explicit(spec):
    with pytest.raises(ValueError):
        load_plan_factory(spec)


def test_cli_missing_database_fails_without_echoing_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HOPE_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["--factory", "reviewed:factory", "--migrations-dir", str(tmp_path), "--status-file", str(tmp_path / "health")])
    assert exc.value.code == 2
    assert "DATABASE_CONFIGURATION_REQUIRED" in capsys.readouterr().err


def test_schema_check_never_backfills_or_accepts_partial_inventory(tmp_path):
    import hashlib
    from sqlalchemy import create_engine, text
    engine = create_engine("sqlite://")
    migration = tmp_path / "001.sql"
    migration.write_text("SELECT 1;", encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE hope_schema_migrations(version TEXT,checksum TEXT)"))
        connection.execute(text("INSERT INTO hope_schema_migrations VALUES ('001.sql',NULL)"))
        with pytest.raises(RuntimeError, match="SCHEMA_INCOMPATIBLE"):
            service.verify_schema_compatibility(connection, tmp_path)
        assert connection.execute(text("SELECT checksum FROM hope_schema_migrations")).scalar_one() is None
        checksum = hashlib.sha256(migration.read_bytes()).hexdigest()
        connection.execute(text("UPDATE hope_schema_migrations SET checksum=:value"), {"value": checksum})
        service.verify_schema_compatibility(connection, tmp_path)
        migration.write_text("SELECT 2;", encoding="utf-8")
        with pytest.raises(RuntimeError, match="SCHEMA_INCOMPATIBLE"):
            service.verify_schema_compatibility(connection, tmp_path)
    engine.dispose()


def test_health_commit_is_validated_and_stopped_without_admission(tmp_path, monkeypatch):
    stop = Event()
    stop.set()
    for commit, expected in [("b" * 40, "b" * 40), ("secret-password", None)]:
        monkeypatch.setenv("HOPE_DEPLOYED_COMMIT", commit)
        target = tmp_path / "health.json"
        assert service.run_service(None, None, migrations_dir=tmp_path, status_file=target, stop_event=stop) == 0
        value = json.loads(target.read_text())
        assert value["deployed_commit"] == expected and value["schema_compatibility"] == "UNKNOWN"
        assert value["state"] == "STOPPED" and value["cycles_succeeded"] == 0


def test_factory_requires_callable_and_cli_sanitizes_startup(tmp_path, monkeypatch, capsys):
    import sys
    monkeypatch.setitem(sys.modules, "reviewed_service_fixture", SimpleNamespace(factory=lambda *_: None, invalid=1))
    assert callable(load_plan_factory("reviewed_service_fixture:factory"))
    with pytest.raises(ValueError, match="NOT_CALLABLE"):
        load_plan_factory("reviewed_service_fixture:invalid")
    monkeypatch.setenv("HOPE_DATABASE_URL", "secret-invalid-database-url")
    with pytest.raises(SystemExit) as exc:
        main(["--factory", "reviewed_service_fixture:factory", "--migrations-dir", str(tmp_path), "--status-file", str(tmp_path / "health")])
    assert exc.value.code == 2
    output = capsys.readouterr().err
    assert "PAPER_SERVICE_STARTUP_FAILED" in output and "secret" not in output


@pytest.mark.parametrize("kind", ["outside", "reexported", "missing_source"])
def test_factory_must_be_defined_inside_exact_release(tmp_path, monkeypatch, kind):
    import hope.api.paper_service as cli
    release = tmp_path / "release"
    source = release / "src" / "reviewed_factory.py"
    source.parent.mkdir(parents=True)
    source.write_text("reviewed = True\n")
    outside = tmp_path / "outside.py"
    outside.write_text("unreviewed = True\n")
    def factory(*_):
        return None
    factory.__module__ = "dependency" if kind == "reexported" else "reviewed_factory"
    imported = SimpleNamespace(factory=factory,
        __file__=None if kind == "missing_source" else str(outside if kind == "outside" else source))
    monkeypatch.setattr(cli.importlib, "import_module", lambda _: imported)
    with pytest.raises(ValueError, match="FACTORY_RELEASE_MISMATCH"):
        cli.load_plan_factory("reviewed_factory:factory", release=release)


def test_factory_defined_inside_exact_release_is_accepted(tmp_path, monkeypatch):
    import hope.api.paper_service as cli
    release = tmp_path / "release"
    source = release / "src" / "reviewed_factory.py"
    source.parent.mkdir(parents=True)
    source.write_text("reviewed = True\n")
    def factory(*_):
        return None
    factory.__module__ = "reviewed_factory"
    monkeypatch.setattr(cli.importlib, "import_module",
        lambda _: SimpleNamespace(factory=factory,__file__=str(source)))
    assert cli.load_plan_factory("reviewed_factory:factory",release=release) is factory


def test_production_cli_always_requires_certified_work(tmp_path, monkeypatch):
    import hope.api.paper_service as cli
    captured = {}
    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setenv("HOPE_DATABASE_URL", "test-only")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: engine)
    monkeypatch.setattr(cli, "load_plan_factory", lambda _, **kwargs: lambda *a: None)
    def run(*a, **kwargs):
        captured.update(kwargs)
        return 0
    monkeypatch.setattr(cli, "run_service", run)
    assert cli.main(["--factory", "reviewed:factory", "--migrations-dir", str(tmp_path),
                     "--status-file", str(tmp_path / "health"), "--once"]) == 0
    assert captured["require_certified"] is True


@pytest.mark.parametrize("primary", ["PAPER_STRATEGY_AUTHORIZATION_INELIGIBLE",
                                    "PAPER_STRATEGY_AUTHORIZATION_RESEARCH_INVALID",
                                    "secret database payload"])
def test_eligibility_failure_exposes_only_fixed_database_codes(primary):
    error = OperationalError("secret SQL", {}, SimpleNamespace(diag=SimpleNamespace(message_primary=primary)))
    state, code = service._failure_state(error)
    assert state == "UNAVAILABLE"
    assert code == (primary if primary.startswith("PAPER_STRATEGY_AUTHORIZATION_") else "PAPER_SERVICE_DATABASE_UNAVAILABLE")


@pytest.mark.parametrize("exception", [RuntimeError("PAPER_CERTIFIED_secret-payload"),
    ValueError("VERIFIED_RESEARCH_SOURCE_secret-payload"), ValueError("RESEARCH_RUN_EVIDENCE_secret-payload"),
    ValueError("BACKTEST_EVIDENCE_secret-payload")])
def test_certified_input_failures_are_explicit_without_echoing_payload(exception):
    assert service._failure_state(exception) == ("UNAVAILABLE", "PAPER_CERTIFIED_INPUT_UNAVAILABLE")


@pytest.mark.parametrize("stage", ["connect", "send"])
def test_notifier_transport_failure_is_bounded_closed_and_sanitized(monkeypatch, stage):
    events = []
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): events.append("closed")
        def settimeout(self, seconds): events.append(("timeout", seconds))
        def connect(self, address):
            events.append("connect")
            if stage == "connect": raise TimeoutError("private socket path")
        def sendall(self, payload):
            events.append("send")
            raise OSError("private transport details")
    monkeypatch.setattr(service.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(service.socket, "socket", lambda *_: Client())
    with pytest.raises(RuntimeError) as error:
        service.SystemdNotifier("/run/systemd/notify").watchdog()
    assert str(error.value) == "PAPER_SERVICE_NOTIFY_FAILED"
    assert events[0] == ("timeout", 1.0)
    assert events[-1] == "closed"


def test_notifier_absence_does_not_open_socket(monkeypatch):
    def forbidden(*args): raise AssertionError("socket must not open")
    monkeypatch.setattr(service.socket, "socket", forbidden)
    service.SystemdNotifier(None).ready()


@pytest.mark.parametrize("address,usec", [("@", "1"), ("/run/notify\0other", "1"),
    ("/run/notify", "9" * 5000)])
def test_notifier_rejects_ambiguous_address_and_unbounded_numeric_input(monkeypatch, address, usec):
    # getenv is mocked because OS environment APIs themselves reject NUL.
    values = {"NOTIFY_SOCKET": address, "WATCHDOG_USEC": usec}
    monkeypatch.setattr(service.os, "getenv", values.get)
    with pytest.raises(RuntimeError, match="^PAPER_SERVICE_NOTIFY_CONFIGURATION_INVALID$"):
        service.SystemdNotifier.from_environment()


@pytest.mark.skipif(os.name != "posix", reason="Native Unix queue saturation runs on Linux CI")
def test_full_notify_queue_fails_within_transport_deadline(tmp_path):
    import inspect
    import subprocess
    import sys
    from contextlib import ExitStack
    path = str(tmp_path / "full.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(path)
        with ExitStack() as producers:
            # One producer can exhaust its own send budget while the receiver
            # still admits a fresh sender. Only a fresh sender's first enqueue
            # failing establishes the receiver saturation required by this test.
            saturated = False
            for _ in range(32):
                filler = producers.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM))
                filler.setblocking(False)
                filler.connect(path)
                sent = 0
                for _ in range(4096):
                    try:
                        filler.send(b"occupied")
                        sent += 1
                    except BlockingIOError:
                        break
                else:
                    pytest.fail("producer did not block within the message bound")
                if sent == 0:
                    saturated = True
                    break
            assert saturated, "receiver not saturated within the producer bound"
            # Keep the producer alive throughout the saturation probe. Execute
            # the actual class in isolation, without importing the whole runtime
            # in the child or depending on pytest's import-path configuration.
            code = ("from __future__ import annotations\n"
                    "from dataclasses import dataclass\nimport os,socket,sys,time\n" +
                    inspect.getsource(service.SystemdNotifier) + """
start=time.monotonic()
try:
    SystemdNotifier(sys.argv[1]).watchdog()
except RuntimeError as error:
    assert str(error) == 'PAPER_SERVICE_NOTIFY_FAILED'
else:
    raise AssertionError('full queue notification unexpectedly succeeded')
assert time.monotonic()-start < 3
print('BOUNDED_QUEUE_FAILURE')
""")
            result = subprocess.run([sys.executable, "-c", code, path],
                                    capture_output=True, text=True, timeout=5)
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == "BOUNDED_QUEUE_FAILURE"
