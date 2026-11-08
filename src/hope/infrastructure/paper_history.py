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
def publisher_lease(status_path):
    """Acquire ownership before reading history or publishing any shared state."""
    import fcntl
    status_path=Path(status_path).absolute()
    path=Path(str(status_path)+".history.json");path.parent.mkdir(parents=True,exist_ok=True)
    parent=path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o022:
        raise RuntimeError("PAPER_RUNTIME_HISTORY_UNVERIFIED")
    lock=Path(str(status_path)+".history.lock")
    descriptor=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,0o640)
    active=[True]
    owner_pid=os.getpid()
    try:
        info=os.fstat(descriptor);named=lock.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) not in {0o600,0o640}
                or info.st_nlink != 1 or (info.st_dev,info.st_ino) != (named.st_dev,named.st_ino)):
            raise RuntimeError("PAPER_RUNTIME_HISTORY_UNVERIFIED")
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError("PAPER_RUNTIME_HISTORY_CONCURRENT") from None
        # Match the existing health-file read-only operations group. Never
        # change permissions until ownership is acquired; no group writer.
        os.fchmod(descriptor,0o640);info=os.fstat(descriptor)
        from hope.api.operations_deployment import _process_identity
        owner_sha=digest((owner_pid,_process_identity(owner_pid)))
        instance=uuid4().hex
        header=canonical(dict(schema='hope.paper-publisher.v1',pid=owner_pid,identity_sha256=owner_sha,instance=instance))
        os.ftruncate(descriptor,0)
        if os.write(descriptor,header)!=len(header):raise RuntimeError('PAPER_RUNTIME_HISTORY_UNVERIFIED')
        os.fsync(descriptor)
        def check_lease():
            if not active[0] or os.getpid()!=owner_pid:raise RuntimeError("PAPER_RUNTIME_HISTORY_UNVERIFIED")
            opened=os.fstat(descriptor);directory=path.parent.lstat()
            current=lock.lstat()
            if ((current.st_dev,current.st_ino,current.st_mode,current.st_uid,current.st_nlink) != (
                    info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink)
                    or (opened.st_dev,opened.st_ino) != (info.st_dev,info.st_ino)
                    or (directory.st_dev,directory.st_ino,directory.st_mode,directory.st_uid) != (
                        parent.st_dev,parent.st_ino,parent.st_mode,parent.st_uid)):
                raise RuntimeError("PAPER_RUNTIME_HISTORY_UNVERIFIED")
        class Publisher:
            check=staticmethod(check_lease)
            def publish(self,target,value,publisher):
                if Path(target).absolute() not in {status_path,path}:
                    raise RuntimeError("PAPER_RUNTIME_PUBLISH_TARGET_INVALID")
                self.check()
                if Path(target).absolute()==status_path:
                    value=dict(value,publisher_instance=instance,publisher_identity_sha256=owner_sha)
                    if 'schema_version' in value:value['schema_version']=2
                publisher(target,value);self.check()
                return value
        yield Publisher()
    finally:
        active[0]=False
        os.close(descriptor)


@contextmanager
def history_lease(status_path,publisher):
    """Compatibility context for lab callers; history writes also own the lease."""
    with publisher_lease(status_path) as lease:
        history=History(Path(str(status_path)+".history.json"),lambda path,value:lease.publish(path,value,publisher))
        history.check_lease=lease.check
        yield history


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


def publisher_observation(status_path,*,expected_instance=None,expected_identity=None):
    """Correlate a held kernel lock, process incarnation and persisted heartbeat."""
    absent=dict(schema='hope.paper-publisher-observation.v1',state='UNKNOWN',identity_sha256=None,
        generated_at=datetime.now(timezone.utc).isoformat(),execution_authorized=False)
    descriptor=None
    try:
        import fcntl
        from hope.api.operations_deployment import _process_identity
        path=Path(str(status_path)+'.history.lock');info=path.lstat()
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
        opened=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) not in {0o600,0o640} or info.st_nlink!=1 or (info.st_dev,info.st_ino)!=(opened.st_dev,opened.st_ino):return absent
        try:
            fcntl.flock(descriptor,fcntl.LOCK_SH|fcntl.LOCK_NB)
            return dict(absent,state='BLOCKED')
        except BlockingIOError:pass
        header=strict_json(os.read(descriptor,1025));health=strict_json(bounded_regular_read(status_path,8192))
        if (set(header)!={'schema','pid','identity_sha256','instance'} or header['schema']!='hope.paper-publisher.v1'
                or (expected_instance is not None and header['instance']!=expected_instance)
                or (expected_identity is not None and header['identity_sha256']!=expected_identity)
                or type(header['pid']) is not int or header['pid']<=0
                or digest((header['pid'],_process_identity(header['pid'])))!=header['identity_sha256']
                or health.get('publisher_instance')!=header['instance']
                or health.get('publisher_identity_sha256')!=header['identity_sha256']):return dict(absent,state='BLOCKED')
        after=path.lstat()
        if (after.st_dev,after.st_ino,after.st_mtime_ns,after.st_ctime_ns)!=(info.st_dev,info.st_ino,info.st_mtime_ns,info.st_ctime_ns):return absent
        return dict(absent,state='VERIFIED',identity_sha256=header['identity_sha256'])
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,ImportError):return absent
    finally:
        if descriptor is not None:os.close(descriptor)
