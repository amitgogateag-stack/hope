"""Bounded crash-durable supervisor observations, never financial authority.

A retained hash chain detects corruption within the window, not a privileged
rollback of the entire file. Durable database claims remain execution authority.
An exclusive lifetime lease prevents competing publishers of one health file.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from hope.api.snapshot_contract import strict_json
from hope.infrastructure.durable_file import bounded_regular_read

LIMIT = 32
STATES = {"STARTING","CURRENT","HALTED","UNAVAILABLE","STOPPED"}


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def validate(value):
    def require(condition):
        if not condition:raise RuntimeError("PAPER_RUNTIME_HISTORY_UNVERIFIED")
    require(isinstance(value,dict) and set(value) == {"schema","anchor","events"}
        and value["schema"] == "hope.paper-history.v1" and isinstance(value["anchor"],str)
        and len(value["anchor"]) == 64 and all(c in "0123456789abcdef" for c in value["anchor"])
        and isinstance(value["events"],list) and 0 < len(value["events"]) <= LIMIT)
    previous = value["anchor"]; sequence = None; last_time = None
    for event in value["events"]:
        require(isinstance(event,dict) and set(event) == {"sequence","instance","generated_at","state","code","previous","sha256"}
            and type(event["sequence"]) is int and 1 <= event["sequence"] <= 9007199254740991
            and (sequence is None or event["sequence"] == sequence+1)
            and isinstance(event["instance"],str) and len(event["instance"]) == 32
            and all(c in "0123456789abcdef" for c in event["instance"])
            and isinstance(event["state"],str) and event["state"] in STATES
            and (event["code"] is None or (isinstance(event["code"],str)
                and event["code"].startswith("PAPER_") and len(event["code"]) <= 80
                and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_" for c in event["code"]))))
        stamp = datetime.fromisoformat(event["generated_at"])
        require(stamp.tzinfo is not None and (last_time is None or stamp >= last_time))
        require(event["previous"] == previous and digest({k:v for k,v in event.items() if k != "sha256"}) == event["sha256"])
        previous = event["sha256"]; sequence = event["sequence"]; last_time = stamp
    return value


def read_history(path):return validate(strict_json(bounded_regular_read(path,32768)))


class History:
    def __init__(self,path,publisher):
        self.path=Path(path);self.publisher=publisher;self.instance=uuid4().hex
        self.value=read_history(path) if self.path.exists() or self.path.is_symlink() else dict(
            schema="hope.paper-history.v1",anchor="0"*64,events=[])
        self.check_lease=lambda:None

    def check(self):
        self.check_lease()
        if self.value["events"]:
            if read_history(self.path) != self.value:raise RuntimeError("PAPER_RUNTIME_HISTORY_UNVERIFIED")
        elif self.path.exists() or self.path.is_symlink():raise RuntimeError("PAPER_RUNTIME_HISTORY_UNVERIFIED")

    def append(self,health):
        # Reread on every publication: unexpected deletion/substitution cannot
        # silently reset a history already observed by this process.
        self.check()
        events = self.value["events"]
        event=dict(sequence=events[-1]["sequence"]+1 if events else 1,instance=self.instance,
            generated_at=health["generated_at"],state=health["state"],code=health["error_code"],
            previous=events[-1]["sha256"] if events else self.value["anchor"])
        event["sha256"]=digest(event)
        result=dict(self.value,events=[*events,event])
        if len(result["events"]) > LIMIT:
            removed=result["events"].pop(0);result["anchor"]=removed["sha256"]
        validate(result);self.publisher(self.path,result);self.value=result


@contextmanager
def history_lease(status_path,publisher):
    """Native production only; no platform fallback can admit execution."""
    import fcntl
    path=Path(str(status_path)+".history.json");path.parent.mkdir(parents=True,exist_ok=True)
    lock=Path(str(status_path)+".history.lock")
    descriptor=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,0o600)
    try:
        info=os.fstat(descriptor);named=lock.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or info.st_nlink != 1 or (info.st_dev,info.st_ino) != (named.st_dev,named.st_ino)):
            raise RuntimeError("PAPER_RUNTIME_HISTORY_UNVERIFIED")
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError("PAPER_RUNTIME_HISTORY_CONCURRENT") from None
        history=History(path,publisher)
        def check_lease():
            current=lock.lstat()
            if (current.st_dev,current.st_ino,current.st_mode,current.st_uid,current.st_nlink) != (
                    info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink):
                raise RuntimeError("PAPER_RUNTIME_HISTORY_UNVERIFIED")
        history.check_lease=check_lease
        yield history
    finally:os.close(descriptor)


def history_observation(path,*,now=None):
    absent=dict(schema="hope.paper-history-observation.v1",state="UNVERIFIED",generated_at=None,
        retained_events=0,retained_starts=0,retained_failures=0,last_state=None,execution_authorized=False)
    try:
        value=read_history(path);events=value["events"];latest=events[-1]
        age=((now or datetime.now(timezone.utc))-datetime.fromisoformat(latest["generated_at"])).total_seconds()
        if age < 0:return absent
        return dict(absent,state="STALE" if age > 300 else "OBSERVED",generated_at=latest["generated_at"],
            retained_events=len(events),retained_starts=sum(e["state"] == "STARTING" for e in events),
            retained_failures=sum(e["state"] in {"UNAVAILABLE","HALTED"} for e in events),last_state=latest["state"])
    except (OSError,ValueError,RuntimeError,TypeError,KeyError):return absent
