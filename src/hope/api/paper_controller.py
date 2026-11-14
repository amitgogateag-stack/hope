"""Durable PAPER verification controller. No activation or execution authority.

The journal is a bounded local audit chain, not an independently signed approval
or protection against privileged restoration of the entire state directory.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import os
from pathlib import Path
import re
import stat
import time

from hope.api.deployment_contract import inspect_deployment
from hope.api.operations_deployment import _activation_guard
from hope.api.runtime_integrity import _sha
from hope.api.snapshot_contract import strict_json
from hope.infrastructure.durable_file import bounded_regular_read
from hope.infrastructure.paper_history import canonical, digest, publisher_lease
from hope.infrastructure.paper_service import publish_service_status

SCHEMA = "hope.paper-controller.v1"
MAX_EVENTS = 32
MAX_SEQUENCE = 9007199254740991
HASH = re.compile(r"[0-9a-f]{64}")
STATES = {"STAGED", "VERIFYING", "VERIFIED", "BLOCKED", "HALTED"}
CODES = {"STAGED", "VERIFYING", "EVIDENCE_VERIFIED", "EVIDENCE_BLOCKED",
         "RECOVERY_REQUIRED", "REVERIFY_REQUIRED", "HALT_LATCHED"}
EDGES = {"STAGED": {"VERIFYING", "HALTED"},
         "VERIFYING": {"VERIFIED", "BLOCKED", "HALTED"},
         "VERIFIED": {"VERIFYING", "BLOCKED", "HALTED"},
         "BLOCKED": {"VERIFYING", "HALTED"}, "HALTED": {"HALTED"}}


def require(condition):
    if not condition:
        raise RuntimeError("PAPER_CONTROLLER_UNVERIFIED")


def is_hash(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def plan(release, receipt, factory):
    _sha(release)
    require(is_hash(receipt) and isinstance(factory, str) and len(factory) <= 200
            and re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", factory, flags=re.ASCII))
    return dict(release_commit=release, receipt_sha256=receipt, factory=factory)


def validate(value):
    require(isinstance(value, dict) and set(value) == {
        "schema", "plan", "anchor", "events", "publisher_instance", "publisher_identity_sha256"}
        and value["schema"] == SCHEMA and isinstance(value["plan"], dict)
        and set(value["plan"]) == {"release_commit", "receipt_sha256", "factory"})
    require(plan(value["plan"]["release_commit"], value["plan"]["receipt_sha256"], value["plan"]["factory"]) == value["plan"])
    require(is_hash(value["anchor"]) and isinstance(value["publisher_instance"], str)
            and re.fullmatch(r"[0-9a-f]{32}", value["publisher_instance"])
            and is_hash(value["publisher_identity_sha256"])
            and isinstance(value["events"], list) and 0 < len(value["events"]) <= MAX_EVENTS)
    previous = value["anchor"]
    last = None
    for event in value["events"]:
        require(isinstance(event, dict) and set(event) == {
            "sequence", "state", "code", "request", "plan_sha256", "observed_at", "expires_at", "evidence_sha256", "previous", "sha256"})
        require(type(event["sequence"]) is int and 1 <= event["sequence"] <= MAX_SEQUENCE
                and isinstance(event["state"], str) and event["state"] in STATES
                and isinstance(event["code"], str) and event["code"] in CODES
                and is_hash(event["request"]) and type(event["observed_at"]) is int and event["observed_at"] >= 0
                and event["plan_sha256"] == digest(value["plan"])
                and event["previous"] == previous
                and event["sha256"] == digest({k: v for k, v in event.items() if k != "sha256"}))
        if last is None and previous == "0" * 64:
            require(event["sequence"] == 1 and event["state"] == event["code"] == "STAGED")
        if last is not None:
            require(event["sequence"] == last["sequence"] + 1 and event["state"] in EDGES[last["state"]]
                    and event["observed_at"] >= last["observed_at"])
        if event["state"] == "VERIFIED":
            require(event["code"] == "EVIDENCE_VERIFIED" and is_hash(event["evidence_sha256"])
                    and type(event["expires_at"]) is int
                    and event["observed_at"] < event["expires_at"] <= event["observed_at"] + 60)
        else:
            require(event["expires_at"] is None and event["evidence_sha256"] is None)
            require(event["code"] in {
                "STAGED": {"STAGED"}, "VERIFYING": {"VERIFYING"},
                "BLOCKED": {"RECOVERY_REQUIRED", "REVERIFY_REQUIRED", "EVIDENCE_BLOCKED"},
                "HALTED": {"HALT_LATCHED"}}[event["state"]])
        previous = event["sha256"]
        last = event
    return value


def read(path):
    path = Path(path)
    require(path.lstat().st_uid == path.parent.lstat().st_uid)
    return validate(strict_json(bounded_regular_read(path, 32768)))


def _directory(directory, *, readonly=False):
    require(os.name == "posix" and directory.resolve(strict=True) == directory)
    for path in (directory, directory.parent):
        info = path.lstat()
        owners = {0, os.geteuid()} if readonly else {os.geteuid()}
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in owners and not info.st_mode & 0o022)


def _proof(report, pinned, now):
    """Accept only the existing deployment collector's complete safe schema."""
    require(isinstance(report, dict) and set(report) == {
        "schema", "state", "release_commit", "image_sha256", "container_id", "process_sha256",
        "runtime_manifest_sha256", "receipt_sha256", "host", "container", "provenance", "provider",
        "rollback", "expires_at", "blockers", "execution_authorized"})
    require(report["schema"] == "hope.deployment-observation.v1" and report["state"] == "VERIFIED"
            and report["release_commit"] == pinned["release_commit"]
            and report["receipt_sha256"] == pinned["receipt_sha256"]
            and report["execution_authorized"] is False and report["blockers"] == []
            and all(report[field] == "VERIFIED" for field in ("host", "container", "provenance", "provider", "rollback"))
            and all(is_hash(report[field]) for field in ("image_sha256", "container_id", "process_sha256", "runtime_manifest_sha256"))
            and type(report["expires_at"]) is int and now < report["expires_at"])
    return digest(report), min(now + 60, report["expires_at"])


def _append(value, state, code, request, now, *, evidence=None, expiry=None):
    result = deepcopy(value)
    events = result["events"]
    last = events[-1] if events else None
    event = dict(sequence=last["sequence"] + 1 if last else 1,
                 state=state, code=code, request=request, observed_at=now,
                 plan_sha256=digest(result["plan"]),
                 evidence_sha256=evidence, expires_at=expiry,
                 previous=last["sha256"] if last else result["anchor"])
    event["sha256"] = digest(event)
    events.append(event)
    if len(events) > MAX_EVENTS:
        result["anchor"] = events.pop(0)["sha256"]
    return validate(result)


def initialize(directory, release, receipt, factory, *, now=None):
    """Explicitly provision a NEW local controller journal; never overwrite one.

    An interruption after mkdir leaves a non-reinitializable directory. Missing
    or corrupt journals require investigation, never implicit legacy adoption.
    """
    pinned = plan(release, receipt, factory)
    directory = Path(directory).absolute()
    require(os.name == "posix" and directory.parent.resolve(strict=True) == directory.parent)
    parent = directory.parent.stat()
    require(parent.st_uid == os.geteuid() and not parent.st_mode & 0o022)
    directory.mkdir(mode=0o750)
    _directory(directory)
    fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    path = directory / "state.json"
    with publisher_lease(path) as lease:
        value = dict(schema=SCHEMA, plan=pinned, anchor="0" * 64, events=[],
                     publisher_instance="0" * 32, publisher_identity_sha256="0" * 64)
        value = _append(value, "STAGED", "STAGED", digest(pinned), int(time.time()) if now is None else now)
        return lease.publish(path, value, publish_service_status)


class Controller:
    def __init__(self, path, root, lease, *, inspector, clock):
        self.path, self.root, self.lease = path, root, lease
        self.inspector, self.clock = inspector, clock
        self.value = read(path)
        self.failed = False

    def _check(self):
        require(not self.failed)
        self.lease.check()
        require(read(self.path) == self.value)

    def _record(self, state, code, request, *, evidence=None, expiry=None):
        self._check()
        try:
            result = _append(self.value, state, code, request, self.clock(), evidence=evidence, expiry=expiry)
            self.value = self.lease.publish(self.path, result, publish_service_status)
        except BaseException:
            # fsync may fail AFTER rename: no retry on an uncertain publication.
            self.failed = True
            raise
        return deepcopy(self.value)

    def recover(self):
        latest = self.value["events"][-1]
        if latest["state"] in {"VERIFYING", "VERIFIED"}:
            self._record("BLOCKED", "RECOVERY_REQUIRED" if latest["state"] == "VERIFYING" else "REVERIFY_REQUIRED", latest["request"])

    def verify(self, request):
        require(is_hash(request))
        self._check()
        if self.value["events"][-1]["state"] == "HALTED":
            raise RuntimeError("PAPER_CONTROLLER_HALT_LATCHED")
        self._record("VERIFYING", "VERIFYING", request)
        pinned = self.value["plan"]
        with ExitStack() as stack:
            try:
                # Keep activation fenced through durable verification commit,
                # not merely through the collector's last source observation.
                stack.enter_context(_activation_guard(self.root, shared=True))
                result = self.inspector(self.root, pinned["release_commit"],
                                        expected_receipt=pinned["receipt_sha256"], expected_factory=pinned["factory"])
                evidence, expiry = _proof(result, pinned, self.clock())
            except Exception:
                # Never retain callback/provider/host text in durable diagnostics.
                return self._record("BLOCKED", "EVIDENCE_BLOCKED", request)
            return self._record("VERIFIED", "EVIDENCE_VERIFIED", request, evidence=evidence, expiry=expiry)

    def halt(self, request):
        require(is_hash(request))
        self._check()
        if self.value["events"][-1]["state"] == "HALTED":
            return deepcopy(self.value)
        return self._record("HALTED", "HALT_LATCHED", request)

    def inspect_handoff(self,request,**options):
        """Owner-scoped read-only authority inspection, never consumes an intent."""
        require(is_hash(request));self._check()
        if self.value['events'][-1]['state']=='HALTED':
            raise RuntimeError('PAPER_CONTROLLER_HALT_LATCHED')
        from hope.api.first_cycle_authorization import observation
        pinned=self.value['plan']
        result=observation(self.root,pinned['release_commit'],request,pinned['receipt_sha256'],pinned['factory'],**options)
        self._check()
        return result


@contextmanager
def controller(directory, root, *, inspector=inspect_deployment, clock=lambda: int(time.time())):
    """One owner for the entire verification/recovery operation; no takeover."""
    path = Path(directory).absolute() / "state.json"
    _directory(path.parent)
    # Do not allow the publisher's convenience mkdir to adopt a missing journal.
    read(path)
    with publisher_lease(path) as lease:
        instance = Controller(path, Path(root), lease, inspector=inspector, clock=clock)
        instance.recover()
        yield instance


def observation(directory, root, *, inspector=inspect_deployment, now=None, expected_plan=None):
    """Bounded projection, fresh independent revalidation, no writes or imports."""
    live_clock = now is None
    now = int(time.time()) if now is None else now
    report = dict(schema="hope.paper-controller-observation.v1", state="UNAVAILABLE", code="JOURNAL_UNVERIFIED",
                  release_commit=None, receipt_sha256=None, sequence=None, retained_events=0,
                  observed_at=now, expires_at=None, execution_authorized=False)
    try:
        directory = Path(directory).absolute()
        _directory(directory, readonly=True)
        path = directory / "state.json"
        value = read(path)
        require(expected_plan is None or value["plan"] == expected_plan)
        latest = value["events"][-1]
        require(latest["observed_at"] <= now)
        report.update(state=latest["state"], code=latest["code"], release_commit=value["plan"]["release_commit"],
                      receipt_sha256=value["plan"]["receipt_sha256"], sequence=latest["sequence"], retained_events=len(value["events"]))
        if latest["state"] == "VERIFYING":
            report.update(state="BLOCKED", code="RECOVERY_REQUIRED")
        elif latest["state"] == "VERIFIED":
            report.update(state="BLOCKED", code="REVERIFY_REQUIRED")
            require(now < latest["expires_at"])
            pinned = value["plan"]
            with _activation_guard(Path(root), shared=True):
                proof = inspector(Path(root), pinned["release_commit"], expected_receipt=pinned["receipt_sha256"], expected_factory=pinned["factory"])
                closing = int(time.time()) if live_clock else now
                require(now <= closing < latest["expires_at"])
                evidence, expiry = _proof(proof, pinned, closing)
                require(evidence == latest["evidence_sha256"] and read(path) == value)
                _directory(directory, readonly=True)
                report.update(state="VERIFIED", code="EVIDENCE_VERIFIED", observed_at=closing, expires_at=min(expiry, latest["expires_at"]))
    except Exception:
        pass
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local PAPER verification journal; never activates services")
    parser.add_argument("action", choices=("initialize", "verify", "halt", "inspect", "inspect-stage"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("/opt/hope-operations"))
    parser.add_argument("--release")
    parser.add_argument("--receipt")
    parser.add_argument("--factory")
    parser.add_argument("--request")
    args = parser.parse_args(argv)
    try:
        if args.action == 'inspect-stage':
            # Read the existing pinned plan without acquiring a publishing
            # lease, recovering a journal, or promoting its lifecycle state.
            directory=args.directory.absolute();_directory(directory,readonly=True)
            value=read(directory/'state.json');pinned=value['plan']
            require(value['events'][-1]['state']!='HALTED')
            from hope.api.deployment_contract import inspect_staged_deployment
            result=inspect_staged_deployment(args.root,pinned['release_commit'],expected_receipt=pinned['receipt_sha256'],expected_factory=pinned['factory'])
            require(read(directory/'state.json')==value)
            print(canonical(result).decode())
            return 0 if result['state']=='STAGED' else 1
        if args.action == "initialize":
            initialize(args.directory, args.release, args.receipt, args.factory)
        elif args.action != "inspect":
            with controller(args.directory, args.root) as owned:
                if args.action == "verify":
                    owned.verify(args.request)
                else:
                    owned.halt(args.request)
        result = observation(args.directory, args.root)
        print(canonical(result).decode())
        return 0 if result["state"] in {"STAGED", "VERIFIED", "HALTED"} else 1
    except Exception:
        print('PAPER_CONTROLLER_UNVERIFIED')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
