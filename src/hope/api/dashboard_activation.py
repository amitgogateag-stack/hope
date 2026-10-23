"""Operator-approved dashboard switch. No PAPER mutation or database writes.

The durable journal is also a boot gate. SIGKILL cannot run rollback code: an
interrupted operation remains blocked until explicit dashboard-only recovery.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC,datetime
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler,build_opener
from uuid import uuid4

from sqlalchemy import create_engine,text

from hope.api import operations_deployment as operations
from hope.api import paper_absence_audit
from hope.api.diagnostic_io import bounded_capture
from hope.api.dashboard_server import _verified_snapshot
from hope.api.m3_audit import _readiness
from hope.api.release_attestation import _NoRedirect,verify_exact_ci
from hope.infrastructure.paper_service import verify_schema_compatibility

OWNER=0
WEB="hope-ops-dashboard-web.service"
EXPORT="hope-ops-dashboard-export.service"
TIMER="hope-ops-dashboard-export.timer"
PAPER="hope-paper.service"
LEGACY_M3="hope-m3-dashboard-web.service"
DASHBOARD_UNITS=(WEB,EXPORT,TIMER)
PHASES={"PREPARED","VERIFYING","COMMITTED","ROLLING_BACK","ROLLBACK_FAILED"}
SAFE_PATH="/usr/sbin:/usr/bin:/sbin:/bin"
SAFE_ENVIRONMENT={"PATH":SAFE_PATH,"LANG":"C","LC_ALL":"C"}
CHILD_ENVIRONMENT_KEYS={"PATH","PYTHONPATH","PYTHONDONTWRITEBYTECODE","PYTHONNOUSERSITE"}


@dataclass(frozen=True)
class Layout:
    root: Path=Path("/opt/hope-operations")
    units: Path=Path("/etc/systemd/system")
    snapshot: Path=Path("/var/lib/hope-dashboard-ops/snapshot.json")
    health: Path=Path("/var/lib/hope-paper/health.json")
    environment: Path=Path("/etc/hope-dashboard-ops.env")

    @property
    def python(self):return self.root/"venv/bin/python"
    @property
    def journal(self):return self.root/"dashboard-activation.json"


def _require(condition,code):
    if not condition:raise RuntimeError("DASHBOARD_"+code)


def _command_environment(value=None):
    value={} if value is None else value
    _require(isinstance(value,dict) and set(value)<=CHILD_ENVIRONMENT_KEYS,
        "SUBPROCESS_ENVIRONMENT_DENIED")
    _require(value.get("PATH",SAFE_PATH)==SAFE_PATH,"SUBPROCESS_PATH_DENIED")
    environment=dict(SAFE_ENVIRONMENT);environment.update(value)
    return environment


def _sha(value):
    _require(isinstance(value,str) and re.fullmatch("[0-9a-f]{40}",value),"SHA_INVALID")
    _require(value!="7e9d341742818a97accce3e2301b1c7f96596795","FROZEN_RELEASE_DENIED")
    return value


def _trusted(path,*,directory=False):
    info=path.lstat()
    _require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and info.st_uid==OWNER and not info.st_mode & 0o022
        and (directory or info.st_nlink==1),"UNTRUSTED_PATH")


def _sync(directory):
    descriptor=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def _write(path,payload,mode=0o644):
    _trusted(path.parent,directory=True)
    if path.exists() or path.is_symlink():_trusted(path)
    descriptor,name=tempfile.mkstemp(prefix=".dashboard-",dir=path.parent)
    try:
        with os.fdopen(descriptor,"wb") as stream:
            os.fchmod(stream.fileno(),mode);stream.write(payload);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path);_sync(path.parent)
    finally:
        if os.path.exists(name):os.unlink(name)


def _json(path):
    _trusted(path)
    _require(path.stat().st_size<=65536,"RECORD_TOO_LARGE")
    payload=path.read_bytes();_require(len(payload)<=65536,"RECORD_TOO_LARGE")
    value=json.loads(payload);_require(isinstance(value,dict),"RECORD_INVALID")
    return value


def _save(path,value,mode=0o644):
    _write(path,json.dumps(value,sort_keys=True,separators=(",",":")).encode(),mode)


def _validate_record(value):
    _require(set(value)=={"schema","target","shared","previous","phase","backup_id",
        "timer_active","pid","boot","ticks"} and value["schema"]=="hope.dashboard-activation.v1",
        "RECORD_INVALID")
    _sha(value["target"]);_sha(value["shared"])
    if value["previous"] is not None:_sha(value["previous"])
    _require(value["phase"] in PHASES and type(value["timer_active"]) is bool
        and type(value["pid"]) is int and value["pid"]>0
        and isinstance(value["backup_id"],str) and re.fullmatch("[0-9a-f]{32}",value["backup_id"])
        and isinstance(value["boot"],str) and re.fullmatch("[0-9a-f-]{36}",value["boot"])
        and isinstance(value["ticks"],str) and value["ticks"].isdigit(),"RECORD_INVALID")
    return value


def _record(layout):
    if not layout.journal.exists() and not layout.journal.is_symlink():return None
    return _validate_record(_json(layout.journal))


def _owner_alive(record):
    try:return operations._process_identity(record["pid"])==(record["boot"],record["ticks"])
    except FileNotFoundError:return False
    # Permission/unknown identity is not evidence that an owner is dead.


def _pointer(layout,name,*,optional=False):
    path=layout.root/name
    if optional and not path.exists() and not path.is_symlink():return None
    _require(path.is_symlink(),"POINTER_INVALID")
    resolved=path.resolve(strict=True);sha=_sha(resolved.name)
    _require(resolved==layout.root/"releases"/sha,"POINTER_OUTSIDE_RELEASES")
    return sha


def _point(layout,sha):
    path=layout.root/"dashboard-current"
    if sha is None:
        _require(path.is_symlink(),"POINTER_INVALID");path.unlink();_sync(layout.root);return
    temporary=layout.root/(".dashboard-pointer-"+uuid4().hex)
    try:
        os.symlink(layout.root/"releases"/_sha(sha),temporary,target_is_directory=True)
        os.replace(temporary,path);_sync(layout.root)
    finally:
        if temporary.is_symlink():temporary.unlink()


def _legacy_configs(layout,sha):
    base=layout.root/"dashboard-current"
    common=(f"[Service]\nWorkingDirectory={base}\nEnvironment=PYTHONPATH={base}/src\n"
        f"ExecStartPre={layout.python} -B -s -P -m hope.api.dashboard_activation gate --target {sha}\nExecStart=\n")
    return {
        WEB:common+(f"ExecStart={layout.python} -B -s -P -m hope.api.dashboard_server --port 8788 "
            f"--preview {base}/docs/dashboard/preview.html --snapshot {layout.snapshot} "
            f"--operations-status-file {layout.health} --deployed-commit {sha} --activation-gate\n"),
        EXPORT:common+(f"ExecStart={layout.python} -B -s -P -m hope.api.dashboard_snapshot --output {layout.snapshot} "
            f"--deployed-commit {sha}\n"),
    }


def _configs(layout,sha):
    """Set the reviewed path after systemd applies UnsetEnvironment.

    Environment= cannot override UnsetEnvironment in a base unit. /usr/bin/env
    adds only this reviewed import path after systemd has scrubbed ambient input.
    """
    path=f"PYTHONPATH={layout.root}/dashboard-current/src"
    unset=("UnsetEnvironment=PYTHONPATH PYTHONHOME PYTHONUSERBASE PYTHONWARNINGS "
        "PYTHONINSPECT PYTHONSTARTUP PYTHONBREAKPOINT PYTHONPLATLIBDIR "
        "LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT\n")
    return {unit:payload.replace(f"Environment={path}\n",unset)
        .replace(f"ExecStartPre={layout.python}",f"ExecStartPre=/usr/bin/env {path} {layout.python}")
        .replace(f"ExecStart={layout.python}",f"ExecStart=/usr/bin/env {path} {layout.python}")
        for unit,payload in _legacy_configs(layout,sha).items()}


def _config_path(layout,unit):return layout.units/(unit+".d")/"90-hope-dashboard-only.conf"


def _check_configs(layout,sha):
    expected=_configs(layout,sha) if sha else {WEB:None,EXPORT:None}
    actual={}
    for unit,payload in expected.items():
        path=_config_path(layout,unit)
        if payload is None:
            _require(not path.exists() and not path.is_symlink(),"UNEXPECTED_OVERRIDE")
            actual[unit]=None
        else:
            _trusted(path.parent,directory=True)
            _trusted(path);actual[unit]=path.read_text()
    # Accept only a complete reviewed pair. Preserve exact prior bytes for
    # rollback; a mixed legacy/new pair or arbitrary override is never adopted.
    _require(actual==expected or (sha and actual==_legacy_configs(layout,sha)),"OVERRIDE_CHANGED")
    return actual


def _install_configs(layout,sha):
    for unit,payload in _configs(layout,sha).items():
        path=_config_path(layout,unit)
        if not path.parent.exists():path.parent.mkdir(mode=0o755);_sync(layout.units)
        _write(path,payload.encode())


def _backup(layout,record):
    value=_json(layout.root/("dashboard-backup-"+record["backup_id"]+".json"))
    _require(set(value)=={"previous_record","configs"} and isinstance(value["configs"],dict),"BACKUP_INVALID")
    expected=_configs(layout,record["previous"]) if record["previous"] else {WEB:None,EXPORT:None}
    _require(value["configs"]==expected or (record["previous"]
        and value["configs"]==_legacy_configs(layout,record["previous"])),"BACKUP_INVALID")
    previous=value["previous_record"]
    if previous is None:
        _require(record["previous"] is None,"BACKUP_INVALID")
    else:
        try:_validate_record(previous)
        except (KeyError,TypeError,RuntimeError):raise RuntimeError("DASHBOARD_BACKUP_INVALID") from None
        _require(previous["phase"]=="COMMITTED" and previous["target"]==record["previous"]
            and previous["shared"]==record["shared"],"BACKUP_INVALID")
    return value


def _idle(layout):
    _trusted(layout.root,directory=True)
    _require(not (layout.root/".activation.lock").exists()
        and not (layout.root/".activation.lock").is_symlink(),"FULL_ACTIVATION_PENDING")
    _require(not any((layout.root/name).exists() or (layout.root/name).is_symlink()
        for name in (".dashboard-activation.lock",".dashboard-current.next","dashboard-deployed.env")),
        "LEGACY_DASHBOARD_REQUIRES_REVIEW")


def _code_identity(layout,target):
    expected=layout.root/"releases"/target
    from hope.api import dashboard_server
    _require(Path(__file__).resolve().parents[3]==expected
        and Path(dashboard_server.__file__).resolve().parents[3]==expected
        and os.environ.get("PYTHONPATH")==str(layout.root/"dashboard-current/src"),"CODE_IDENTITY_MISMATCH")


def runtime_gate(layout,target):
    """Read-only request/start fence, including death after service startup."""
    _idle(layout)
    _trusted(layout.root/"releases",directory=True);_trusted(layout.units,directory=True)
    _code_identity(layout,target)
    record=_record(layout);_require(record is not None,"JOURNAL_MISSING")
    _require(record["phase"]!="ROLLBACK_FAILED","RECOVERY_REQUIRED")
    expected=record["previous"] if record["phase"]=="ROLLING_BACK" else record["target"]
    _require(expected==_sha(target) and _pointer(layout,"current")==record["shared"]
        and _pointer(layout,"dashboard-current")==target,"GATE_POINTER_MISMATCH")
    _check_configs(layout,target)
    _require(record["phase"]=="COMMITTED" or _owner_alive(record),"RECOVERY_REQUIRED")
    return {"status":"GATE_PASSED","dashboard_commit":target,
        "paper_commit":record["shared"],"execution_authorized":False}


@contextmanager
def runtime_view(layout,target):
    """Hold a stable activation-state observation for one dashboard request."""
    with operations._activation_guard(layout.root,shared=True):
        yield runtime_gate(layout,target)


def gate(layout,target,host):
    """Scoped systemd startup additionally proves PAPER cannot boot/start."""
    result=runtime_gate(layout,target);host.paper_safe();return result


def _rollback(layout,record,host):
    backup=_backup(layout,record)
    _require(_pointer(layout,"current")==record["shared"],"SHARED_POINTER_CHANGED")
    # Validate the prior code/runtime/database before any attempt to restart it.
    host.rollback_preflight(record["previous"] or record["shared"])
    record["phase"]="ROLLING_BACK";_save(layout.journal,record)
    try:
        host.stop()
        current=_pointer(layout,"dashboard-current",optional=True)
        _require(current in {record["target"],record["previous"]},"POINTER_CHANGED")
        if current!=record["previous"]:_point(layout,record["previous"])
        for unit,payload in backup["configs"].items():
            path=_config_path(layout,unit)
            if payload is None:
                if path.exists():_trusted(path);path.unlink();_sync(path.parent)
            else:_write(path,payload.encode())
        host.reload();host.start(record["timer_active"])
        host.verify(record["previous"] or record["shared"],managed=record["previous"] is not None,require_fresh=False)
        host.paper_safe()
        _save(layout.root/("dashboard-rollback-"+record["backup_id"]+".json"),
            {"status":"ROLLED_BACK","target":record["target"],"restored":record["previous"] or record["shared"]},0o600)
        if backup["previous_record"] is None:
            layout.journal.unlink();_sync(layout.root)
        else:_save(layout.journal,backup["previous_record"])
    except BaseException:
        record["phase"]="ROLLBACK_FAILED";_save(layout.journal,record)
        host.stop()
        raise


def activate(layout,host,*,target,shared,ci_run,ci_attempt):
    target,shared=_sha(target),_sha(shared)
    with operations._activation_guard(layout.root):
        _idle(layout)
        previous_record=_record(layout)
        _require(previous_record is None or previous_record["phase"]=="COMMITTED","RECOVERY_REQUIRED")
        previous=_pointer(layout,"dashboard-current",optional=True)
        _require(_pointer(layout,"current")==shared and
            ((previous_record is None and previous is None) or
             (previous_record is not None and previous_record["target"]==previous
              and previous_record["shared"]==shared)),"EXPECTED_POINTER_MISMATCH")
        _require(target!=(previous or shared),"ALREADY_ACTIVE")
        previous_configs=_check_configs(layout,previous)
        timer_active=host.preflight(target,shared,previous,ci_run,ci_attempt)
        boot,ticks=operations._process_identity(os.getpid())
        record=dict(schema="hope.dashboard-activation.v1",target=target,shared=shared,
            previous=previous,phase="PREPARED",backup_id=uuid4().hex,timer_active=timer_active,
            pid=os.getpid(),boot=boot,ticks=ticks)
        _save(layout.root/("dashboard-backup-"+record["backup_id"]+".json"),
            {"previous_record":previous_record,"configs":previous_configs},0o600)
        _save(layout.journal,record)
        try:
            host.stop();_install_configs(layout,target);_point(layout,target)
            record["phase"]="VERIFYING";_save(layout.journal,record)
            host.reload();host.start(timer_active);host.verify(target,managed=True);host.paper_safe()
            record["phase"]="COMMITTED";_save(layout.journal,record)
        except BaseException:
            try:
                # Quiesce an uncommitted target even if backup/runtime/PAPER
                # prerequisites prevent restoration. Stopping dashboard units
                # is always allowed; no PAPER unit is touched.
                host.stop()
                _rollback(layout,record,host)
            except BaseException:
                record["phase"]="ROLLBACK_FAILED";_save(layout.journal,record)
                raise
            raise
        return {"status":"DASHBOARD_SWITCH_VERIFIED","dashboard_commit":target,
            "shared_commit":shared,"paper_activated":False,"timer_active":timer_active}


def rollback(layout,host,*,target,shared):
    with operations._activation_guard(layout.root):
        _idle(layout);record=_record(layout)
        _require(record is not None and record["target"]==_sha(target)
            and record["shared"]==_sha(shared),"RECOVERY_IDENTITY_MISMATCH")
        _require(record["phase"]=="COMMITTED" or not _owner_alive(record),"OWNER_STILL_ACTIVE")
        host.paper_safe()
        # Transfer the recovery lease so the scoped pre-start gate can verify it.
        record["pid"]=os.getpid();record["boot"],record["ticks"]=operations._process_identity(os.getpid())
        _rollback(layout,record,host)
        return {"status":"DASHBOARD_ROLLED_BACK","dashboard_commit":record["previous"] or shared,
            "paper_activated":False}


class Host:
    def __init__(self,layout):self.layout=layout;self.stage="INITIAL"

    def run(self,*args,timeout=20,env=None):
        return subprocess.run(args,check=True,capture_output=True,text=True,timeout=timeout,
            env=_command_environment(env)).stdout

    def show(self,unit):
        _require(unit in (*DASHBOARD_UNITS,PAPER),"UNIT_DENIED")
        output=self.run("systemctl","show",unit,"--property=ActiveState,UnitFileState,MainPID,FragmentPath,DropInPaths")
        return dict(line.split("=",1) for line in output.splitlines() if "=" in line)

    def control(self,verb,*units):
        _require(verb in {"stop","start","reset-failed"} and units and all(u in DASHBOARD_UNITS for u in units),"SERVICE_MUTATION_DENIED")
        self.run("systemctl",verb,*units,timeout=90)

    def paper_safe(self):
        state=paper_absence_audit.unit_state(self.paper_properties())
        _require(state!="ABSENT_REQUIRES_REVIEW","PAPER_ABSENCE_REQUIRES_HOST_REVIEW")
        _require(state=="INSTALLED_INACTIVE","PAPER_MUST_BE_INACTIVE_DISABLED")
        self.legacy_safe()

    def audit_run(self,*args,timeout=10,max_output=65536):
        lists={("systemctl",verb,"--all","--no-pager","--output=json")
            for verb in ("list-units","list-unit-files")}
        queries={"--property="+",".join(paper_absence_audit.PROPERTIES),
            "--property=Id,LoadState,ActiveState,ExecStart,FragmentPath,DropInPaths"}
        allowed=args in lists or (len(args)==4 and args[:2]==("systemctl","show")
            and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_@.:\\-]*\.service",args[2])
            and args[3] in queries)
        _require(allowed,"DIAGNOSTIC_COMMAND_DENIED")
        return bounded_capture(("/usr/bin/systemctl",*args[1:]),timeout=timeout,
            max_output=max_output,env=SAFE_ENVIRONMENT)

    def paper_properties(self,*,run=None):
        try:
            command=("systemctl","show",PAPER,"--property="+",".join(paper_absence_audit.PROPERTIES))
            return self.run(*command) if run is None else run(*command,timeout=10,max_output=65536)
        except subprocess.CalledProcessError as exc:
            # A nonzero response is usable only as a complete, exact absent
            # record. It still cannot satisfy the activation gate.
            _require(exc.returncode in {1,4} and
                paper_absence_audit.unit_state(exc.stdout)=="ABSENT_REQUIRES_REVIEW",
                "PAPER_UNIT_UNVERIFIED")
            return exc.stdout

    def absence_deployment_check(self):
        _idle(self.layout)
        value=_record(self.layout)
        _require(value is None or value["phase"]=="COMMITTED","RECOVERY_REQUIRED")
        shared=_pointer(self.layout,"current")
        if value is not None:
            _require(value["shared"]==_pointer(self.layout,"current")
                and value["target"]==_pointer(self.layout,"dashboard-current"),"GATE_POINTER_MISMATCH")
        with paper_absence_audit.deployment_view(self.layout.root):
            _idle(self.layout)
            _require(_record(self.layout)==value and _pointer(self.layout,"current")==shared,"DEPLOYMENT_CHANGED")
        return (0,)  # No conflicts seen; never an absence attestation.

    def legacy_safe(self):
        try:output=self.run("systemctl","show",LEGACY_M3,"--property=LoadState,ActiveState,UnitFileState")
        except subprocess.CalledProcessError as exc:
            _require(exc.returncode in {1,4} and isinstance(exc.stdout,str)
                and "LoadState=not-found" in exc.stdout.splitlines(),"LEGACY_DASHBOARD_UNVERIFIED")
            output=exc.stdout
        value=dict(line.split("=",1) for line in output.splitlines() if "=" in line)
        _require(value.get("LoadState")=="not-found" or (value.get("LoadState")=="loaded"
            and value.get("ActiveState")=="inactive" and value.get("UnitFileState") in {"disabled","masked"}),
            "LEGACY_DASHBOARD_MUST_BE_INACTIVE_DISABLED")

    def stop(self):
        self.control("stop",TIMER,EXPORT,WEB)
        _require(all(self.show(u).get("ActiveState") in {"inactive","failed"} for u in DASHBOARD_UNITS),"STOP_UNCONFIRMED")

    def reload(self):self.run("systemctl","daemon-reload")

    def start(self,timer_active):
        self.paper_safe();self.control("reset-failed",*DASHBOARD_UNITS)
        # Force a target-release read-only export before the web process can
        # represent the prior release's still-fresh snapshot as current.
        self.control("start",EXPORT);self.control("start",WEB)
        if timer_active:self.control("start",TIMER)

    def release(self,sha):
        release=self.layout.root/"releases"/_sha(sha)
        _trusted(self.layout.root/"releases",directory=True)
        _trusted(release,directory=True)
        _require((release/".git").is_dir() and not (release/".git/commondir").exists()
            and not (release/".git/objects/info/alternates").exists(),"EXTERNAL_GIT_STATE_DENIED")
        # Git configuration and ignored Python files must not be writable inputs.
        count=0;started=time.monotonic()
        for directory,dirs,files in os.walk(release,followlinks=False):
            count+=len(dirs)+len(files)
            _require(count<=50000 and time.monotonic()-started<=30,"RELEASE_SCAN_LIMIT")
            for name in dirs:_trusted(Path(directory)/name,directory=True)
            for name in files:_trusted(Path(directory)/name)
        operations.inspect_release(release,sha)
        return release

    def runtime(self,release,*,new=False):
        layout=self.layout
        _trusted(layout.python.resolve(strict=True))
        for path in (layout.root/"venv",layout.root/"venv/bin"):_trusted(path,directory=True)
        count=0;started=time.monotonic()
        for directory,dirs,files in os.walk(layout.root/"venv",followlinks=False):
            count+=len(dirs)+len(files)
            _require(count<=50000 and time.monotonic()-started<=30,"RUNTIME_SCAN_LIMIT")
            for name in (*dirs,*files):
                path=Path(directory)/name
                if path.is_symlink():
                    _require(path.lstat().st_uid==OWNER,"UNTRUSTED_RUNTIME_LINK")
                    path=path.resolve(strict=True)
                _trusted(path,directory=path.is_dir())
        code=("import sys;from pathlib import Path;import sqlalchemy,psycopg;"
            "from hope.api import dashboard_server,dashboard_snapshot;"
            +("from hope.api import dashboard_readiness,dashboard_activation;" if new else "")+
            "from hope.api.dashboard_server import _verified_snapshot;"
            "assert sys.version_info[:2]==(3,13);"
            f"assert Path(dashboard_server.__file__).resolve().is_relative_to(Path({str(release)!r}));"
            f"Path({str(release/'docs/dashboard/preview.html')!r}).read_bytes();"
            +(f"_verified_snapshot(Path({str(layout.snapshot)!r}));" if new else "")+
            "print('DASHBOARD_RUNTIME_VERIFIED')")
        result=self.run("runuser","--user","hope-dashboard","--",str(layout.python),"-B","-s","-P","-c",code,
            env={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","PYTHONPATH":str(release/"src"),"PYTHONDONTWRITEBYTECODE":"1","PYTHONNOUSERSITE":"1"})
        _require(result.strip()=="DASHBOARD_RUNTIME_VERIFIED","RUNTIME_UNVERIFIED")

    def database(self,release):
        layout=self.layout;_trusted(layout.environment)
        _require(layout.environment.stat().st_size<=65536,"ENVIRONMENT_TOO_LARGE")
        with layout.environment.open(encoding="utf-8") as stream:content=stream.read(65537)
        _require(len(content)<=65536,"ENVIRONMENT_TOO_LARGE");values={}
        for line in content.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):continue
            key,separator,value=line.partition("=");_require(bool(separator),"ENVIRONMENT_INVALID")
            _require(key in {"HOPE_DATABASE_URL","HOPE_LIVE_ENABLED","HOPE_EXECUTION_MODE"},"ENVIRONMENT_KEY_DENIED")
            _require(key not in values,"ENVIRONMENT_INVALID");values[key]=value.strip().strip("\"'")
        _require(values.get("HOPE_DATABASE_URL") and values.get("HOPE_LIVE_ENABLED","false")=="false"
            and values.get("HOPE_EXECUTION_MODE","PAPER")=="PAPER","ENVIRONMENT_INVALID")
        engine=create_engine(values["HOPE_DATABASE_URL"],connect_args={"connect_timeout":10,
            "options":"-c statement_timeout=15000 -c lock_timeout=1000"})
        try:
            with engine.connect() as connection:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                _require(connection.execute(text("SELECT current_database()")).scalar_one()=="hope_m3_restore_test","WRONG_DATABASE")
                version=int(connection.execute(text("SHOW server_version_num")).scalar_one())
                _require(version//10000 in {16,17},"DATABASE_VERSION_UNVERIFIED")
                operations.verify_database_role(connection,component="dashboard")
                verify_schema_compatibility(connection,release/"migrations")
                self.postgres_version_num=version
        finally:engine.dispose()

    def configuration(self,shared,previous):
        layout=self.layout;_trusted(layout.units,directory=True)
        _check_configs(layout,previous)
        for unit in DASHBOARD_UNITS:
            path=layout.units/unit;_trusted(path)
            _require(path.read_bytes()==(layout.root/"releases"/shared/"deploy/operations"/unit).read_bytes(),"BASE_UNIT_CHANGED")
            value=self.show(unit)
            expected=str(_config_path(layout,unit)) if previous and unit in {WEB,EXPORT} else ""
            _require(value.get("FragmentPath")==str(path) and value.get("DropInPaths")==expected,"UNIT_CONFIGURATION_CHANGED")
        _require(self.show(WEB).get("ActiveState")=="active" and self.show(WEB).get("UnitFileState")=="enabled","ORIGINAL_DASHBOARD_NOT_ENABLED_ACTIVE")
        _require(self.show(TIMER).get("ActiveState") in {"active","inactive"}
            and self.show(EXPORT).get("ActiveState")=="inactive","EXPORT_BUSY")

    def listener(self):
        pid=self.show(WEB).get("MainPID","")
        _require(pid.isdigit() and int(pid)>0,"LISTENER_IDENTITY_UNKNOWN")
        lines=self.run("ss","-H","-ltnp","sport = :8788").splitlines()
        _require(len(lines)==1 and len(lines[0].split())>=6
            and lines[0].split()[3]=="127.0.0.1:8788"
            and re.search(r"\bpid="+pid+r",",lines[0]),"PRIVATE_LISTENER_MISMATCH")
        executable=Path("/proc")/pid/"exe"
        _require(executable.resolve(strict=True)==self.layout.python.resolve(strict=True),"LISTENER_RUNTIME_MISMATCH")

    def rollback_preflight(self,sha):
        self.stage="ROLLBACK_PREFLIGHT"
        self.paper_safe();release=self.release(sha);self.runtime(release);self.database(release)

    def preflight(self,target,shared,previous,ci_run,ci_attempt):
        self.stage="PAPER_INACTIVE"
        self.paper_safe()
        self.stage="RELEASE_TRUST"
        self.release(shared)
        if previous:self.release(previous)
        release=self.release(target)
        _require((release/"src/hope/api/dashboard_activation.py").is_file(),"ACTIVATION_TOOL_MISSING")
        self.stage="GITHUB_ATTESTATION";verify_exact_ci(target,ci_run,ci_attempt)
        self.stage="RUNTIME";self.runtime(release,new=True)
        self.stage="DATABASE_TARGET";self.database(release)
        self.stage="DATABASE_PRIOR"
        self.database(self.layout.root/"releases"/(previous or shared))
        self.stage="SNAPSHOT"
        _,generated=_verified_snapshot(self.layout.snapshot)
        _require(0<=(datetime.now(UTC)-generated).total_seconds()<=3600,"SNAPSHOT_NOT_CURRENT")
        self.stage="RESOURCES"
        _require(shutil.disk_usage(self.layout.root).free>=128*1024*1024,"DISK_INSUFFICIENT")
        memory=Path("/proc/meminfo").read_text()
        match=re.search(r"^MemAvailable:\s+(\d+) kB$",memory,re.M)
        _require(match and int(match.group(1))>=128*1024,"MEMORY_INSUFFICIENT")
        self.stage="SYSTEMD_CONFIGURATION";self.configuration(shared,previous)
        self.stage="PRIVATE_LISTENER";self.listener()
        return self.show(TIMER)["ActiveState"]=="active"

    def request(self,path):
        _require(path in {"/","/status.json","/snapshot.json","/operations.json","/readiness.json","/release.json"},"HTTP_PATH_DENIED")
        try:
            with build_opener(ProxyHandler({}),_NoRedirect()).open("http://127.0.0.1:8788"+path,timeout=5) as response:
                payload=response.read(2*1024*1024+1)
                _require(len(payload)<=2*1024*1024,"HTTP_BODY_TOO_LARGE")
                return response.status,payload
        except HTTPError as exc:
            status=exc.code;exc.close();return status,b""

    def verify(self,sha,*,managed,require_fresh=True):
        # A bounded retry handles normal systemd startup, never changes health.
        deadline=time.monotonic()+90
        for _ in range(15):
            if time.monotonic()>deadline:break
            try:
                self.paper_safe();self.listener()
                status,payload=self.request("/")
                _require(status==200 and payload==(self.layout.root/"releases"/sha/"docs/dashboard/preview.html").read_bytes(),"HTTP_ROOT_UNAVAILABLE")
                status,payload=self.request("/status.json");value=json.loads(payload)
                _require(status==200 and value.get("service")=="RUNNING","HTTP_STATUS_UNAVAILABLE")
                status,payload=self.request("/snapshot.json")
                if not require_fresh and status==503:
                    pass  # Prior reviewed web may truthfully report unavailable data.
                else:
                    snapshot,generated=_verified_snapshot(self.layout.snapshot)
                    _require(status==200 and payload==snapshot,"HTTP_SNAPSHOT_UNAVAILABLE")
                    if require_fresh:
                        _require(0<=(datetime.now(UTC)-generated).total_seconds()<=3600,"SNAPSHOT_NOT_CURRENT")
                        _require(json.loads(snapshot).get("deployed_commit")==sha,
                            "SNAPSHOT_RELEASE_MISMATCH")
                _require(self.request("/operations.json")[0] in {200,503},"HTTP_OPERATIONS_INVALID")
                if managed:
                    status,payload=self.request("/release.json")
                    _require(status==200 and json.loads(payload)==dict(schema="hope.dashboard-release.v1",
                        dashboard_commit=sha,paper_only=True,live_enabled=False,execution_authorized=False),"HTTP_RELEASE_MISMATCH")
                    status,payload=self.request("/readiness.json");_require(status==200,"HTTP_READINESS_UNAVAILABLE")
                    _require("status" not in _readiness(json.loads(payload)),"HTTP_READINESS_INVALID")
                return
            except (OSError,ValueError,RuntimeError):time.sleep(1)
        raise RuntimeError("DASHBOARD_VERIFICATION_TIMEOUT")


def main(argv=None):
    parser=argparse.ArgumentParser(description="Approved dashboard-only activation; never starts PAPER")
    parser.add_argument("action",choices=("preflight","activate","rollback","gate","status","paper-audit"))
    parser.add_argument("--target")
    parser.add_argument("--expected-current")
    parser.add_argument("--ci-run-id",type=int)
    parser.add_argument("--ci-run-attempt",type=int)
    parser.add_argument("--approve-dashboard-change",action="store_true")
    args=parser.parse_args(argv);layout=Layout();host=Host(layout)
    try:
        if args.action in {"activate","rollback"}:
            _require(args.approve_dashboard_change,"EXPLICIT_APPROVAL_REQUIRED")
        _require(sys.platform=="linux","LINUX_REQUIRED")
        if args.action!="gate":_require(os.geteuid()==0,"ROOT_REQUIRED")
        if args.action=="paper-audit":
            print(json.dumps(paper_absence_audit.audit(host),sort_keys=True));return 2
        elif args.action=="status":
            value=_record(layout)
            result={"status":"UNMANAGED" if value is None else value["phase"],
                "dashboard_commit":_pointer(layout,"dashboard-current",optional=True),
                "shared_commit":_pointer(layout,"current"),"execution_authorized":False}
        elif args.action=="gate":result=gate(layout,_sha(args.target),host)
        elif args.action=="rollback":result=rollback(layout,host,target=args.target,shared=args.expected_current)
        elif args.action=="activate":
            result=activate(layout,host,target=args.target,shared=args.expected_current,
                ci_run=args.ci_run_id,ci_attempt=args.ci_run_attempt)
        else:
            _idle(layout);value=_record(layout)
            _require(value is None or value["phase"]=="COMMITTED","RECOVERY_REQUIRED")
            _require(_pointer(layout,"current")==_sha(args.expected_current),"EXPECTED_POINTER_MISMATCH")
            host.preflight(_sha(args.target),args.expected_current,
                _pointer(layout,"dashboard-current",optional=True),args.ci_run_id,args.ci_run_attempt)
            result={"status":"PREFLIGHT_PASSED","host_changes":False,"execution_authorized":False}
            if hasattr(host,"postgres_version_num"):result["postgres_version_num"]=host.postgres_version_num
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        stage=getattr(host,"stage","INITIAL")
        if stage not in {"INITIAL","ROLLBACK_PREFLIGHT","PAPER_INACTIVE","RELEASE_TRUST",
                "GITHUB_ATTESTATION","RUNTIME","DATABASE_TARGET","DATABASE_PRIOR","SNAPSHOT",
                "RESOURCES","SYSTEMD_CONFIGURATION","PRIVATE_LISTENER"}:stage="INITIAL"
        parser.exit(2,"DASHBOARD_OPERATION_BLOCKED stage="+stage+" REVIEW_JOURNAL_AND_PREREQUISITES\n")


if __name__=="__main__":raise SystemExit(main())
