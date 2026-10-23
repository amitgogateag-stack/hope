"""Read-only host reconciliation; emits fixed metadata, never configuration secrets."""
import argparse
from datetime import UTC,datetime
import json
import os
from pathlib import Path
import subprocess
from urllib.error import HTTPError
from urllib.request import ProxyHandler,build_opener

from sqlalchemy import create_engine,text

from hope.api.operations_deployment import inspect_release,verify_database_role
from hope.api.dashboard_server import OPERATIONS_ERROR_CODES
from hope.api.release_attestation import _NoRedirect,verify_exact_ci
from hope.infrastructure.paper_service import verify_schema_compatibility

UNITS=("hope-paper.service","hope-ops-dashboard-web.service","hope-ops-dashboard-export.timer")
ENDPOINTS=("/status.json","/snapshot.json","/operations.json","/readiness.json")
READINESS_FIELDS={
    "server":{"RUNNING"},
    "paper_health":{"READY","HALTED","UNAVAILABLE"},
    "reason":{"HEALTH_MISSING","HEALTH_UNREADABLE","HEALTH_INVALID","HEALTH_STALE",
        "WORKER_STARTING","WORKER_STOPPED","WORKER_UNAVAILABLE","SNAPSHOT_NOT_CURRENT",
        "WORKER_IDENTITY_UNVERIFIED","CLAIMS_REQUIRE_REVIEW","CURRENT_OBSERVATIONS_ONLY",
        "HEALTH_FROM_FUTURE","POLL_NOT_CURRENT","WORKER_STATUS_INCONSISTENT",
        "SNAPSHOT_IDENTITY_UNVERIFIED",
        "DURABLE_HALT_OBSERVED"},
    "worker_report":{"UNKNOWN","STARTING","CURRENT","HALTED","UNAVAILABLE","STOPPED"},
    "snapshot":{"CURRENT","STALE","UNAVAILABLE"},
    "database":{"UNVERIFIED","OBSERVED_AT_SNAPSHOT"},
    "reconciliation":{"UNVERIFIED","CLAIMS_REQUIRE_REVIEW"},
}
SYSTEMCTL="/usr/bin/systemctl"
COMMAND_ENVIRONMENT={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LANG":"C","LC_ALL":"C",
    "HOME":"/nonexistent"}


def _readiness(value):
    """Validate the diagnostic contract without granting execution authority."""
    if (value.get("schema")!="hope.operational-readiness.v1"
            or value.get("paper_only") is not True or value.get("live_enabled") is not False
            or value.get("execution_authorized") is not False):
        raise ValueError("readiness safety contract")
    safe={"schema":"hope.operational-readiness.v1","paper_only":True,
        "live_enabled":False,"execution_authorized":False}
    for key,allowed in READINESS_FIELDS.items():
        if not isinstance(value.get(key),str) or value[key] not in allowed:
            raise ValueError("readiness vocabulary")
        safe[key]=value[key]
    stamps={}
    for key in ("generated_at","last_successful_poll_at","last_successful_paper_at","last_failure_at"):
        stamp=value.get(key)
        if stamp is None and key!="generated_at":
            safe[key]=None
            continue
        if not isinstance(stamp,str):raise ValueError("readiness timestamp")
        parsed=datetime.fromisoformat(stamp.replace("Z","+00:00"))
        if parsed.tzinfo is None:raise ValueError("readiness timestamp timezone")
        stamps[key]=parsed
        safe[key]=parsed.astimezone(UTC).isoformat()
    generated=stamps["generated_at"]
    if any(stamp>generated for key,stamp in stamps.items() if key!="generated_at"):
        raise ValueError("readiness timestamp order")
    age=(datetime.now(UTC)-generated).total_seconds()
    if age < -300:raise ValueError("readiness future timestamp")
    code=value.get("last_failure_code")
    if (code is None)!=(safe["last_failure_at"] is None):
        raise ValueError("readiness failure history")
    safe["last_failure_code"]=(code if isinstance(code,str) and code in OPERATIONS_ERROR_CODES
        else "PAPER_SERVICE_CYCLE_FAILED") if code is not None else None
    if safe["paper_health"]=="READY" and (
            safe["reason"]!="CURRENT_OBSERVATIONS_ONLY" or safe["worker_report"]!="CURRENT"
            or safe["snapshot"]!="CURRENT" or safe["database"]!="OBSERVED_AT_SNAPSHOT"
            or safe["reconciliation"]!="UNVERIFIED"):
        raise ValueError("readiness contradictory ready")
    # Never reproduce an old READY/HALTED claim as a current host observation.
    if age>300:return {"status":"STALE_RESPONSE"}
    if safe["paper_health"]=="READY":
        poll=stamps.get("last_successful_poll_at")
        if age<0 or poll is None or not 0<=age+(generated-poll).total_seconds()<=300:
            raise ValueError("readiness current poll required")
    return safe


def _unit(unit):
    try:
        result=subprocess.run([SYSTEMCTL,"show",unit,"--property=ActiveState,SubState,UnitFileState"],
            capture_output=True,text=True,timeout=10,check=True,env=COMMAND_ENVIRONMENT)
        fields=dict(line.split("=",1) for line in result.stdout.splitlines() if "=" in line)
        return {key:fields.get(key) if fields.get(key) in {
            "active","inactive","failed","activating","deactivating","running","dead","exited","waiting",
            "enabled","disabled","static","indirect","masked","enabled-runtime"} else "UNKNOWN"
            for key in ("ActiveState","SubState","UnitFileState")}
    except Exception:
        return {"status":"UNAVAILABLE"}


def _endpoint(path):
    if path not in ENDPOINTS:
        return {"http_status":None,"status":"INVALID_ENDPOINT"}
    http_status=None
    try:
        with build_opener(ProxyHandler({}),_NoRedirect()).open("http://127.0.0.1:8788"+path,timeout=5) as response:
            http_status=response.status
            body=response.read(32769)
            if len(body)>32768:raise ValueError("oversize")
            value=json.loads(body)
            if not isinstance(value,dict):raise ValueError("shape")
            if path=="/readiness.json":
                return {"http_status":http_status,**_readiness(value)}
            observed={"http_status":response.status}
            allowed={"service":{"RUNNING"},"data_feed":{"CURRENT","STALE","UNAVAILABLE"},
                "state":{"STARTING","CURRENT","HALTED","UNAVAILABLE","STOPPED"},
                "paper_health":{"READY","HALTED","UNAVAILABLE"}}
            for key,values in allowed.items():
                if isinstance(value.get(key),str) and value[key] in values:observed[key]=value[key]
            return observed
    except HTTPError as exc:
        status=exc.code;exc.close()
        return {"http_status":status,"status":"UNAVAILABLE"}
    except Exception:
        return {"http_status":http_status,"status":"INVALID_RESPONSE" if http_status is not None else "UNAVAILABLE"}


def audit(engine,*,release,sha,component,attestation):
    if attestation.get("sha")!=sha:raise ValueError("M3_AUDIT_ATTESTATION_MISMATCH")
    inspect_release(release,sha)
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        if connection.execute(text("SELECT current_database()")).scalar_one()!="hope_m3_restore_test":
            raise RuntimeError("M3_AUDIT_WRONG_DATABASE")
        verify_database_role(connection,component=component)
        verify_schema_compatibility(connection,Path(release)/"migrations")
        version=connection.execute(text("SHOW server_version_num")).scalar_one()
        migrations=connection.execute(text("SELECT count(*) FROM hope_schema_migrations")).scalar_one()
    return dict(schema="hope.m3-audit.v1",release=attestation,database="hope_m3_restore_test",
        postgres_version_num=int(version),migrations=migrations,
        role="SCOPED_PREFLIGHT_PASSED",component=component,
        units={unit:_unit(unit) for unit in UNITS},
        endpoints={path:_endpoint(path) for path in ENDPOINTS},
        host_changes=False,paper_execution=False,operational_acceptance="NOT_ESTABLISHED")


def main(argv=None):
    parser=argparse.ArgumentParser(description="Read-only M3 release/database/service/HTTP audit")
    parser.add_argument("--release",type=Path,required=True)
    parser.add_argument("--expected-sha",required=True)
    parser.add_argument("--ci-run-id",type=int,required=True)
    parser.add_argument("--ci-run-attempt",type=int,required=True)
    parser.add_argument("--component",choices=("paper","dashboard"),required=True)
    args=parser.parse_args(argv);engine=None
    try:
        attestation=verify_exact_ci(args.expected_sha,args.ci_run_id,args.ci_run_attempt)
        engine=create_engine(os.environ["HOPE_DATABASE_URL"],connect_args={"connect_timeout":10,
            "options":"-c statement_timeout=15000 -c lock_timeout=1000"})
        result=audit(engine,release=args.release,sha=args.expected_sha,component=args.component,attestation=attestation)
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        parser.exit(2,"M3_READ_ONLY_AUDIT_BLOCKED\n")
    finally:
        if engine is not None:engine.dispose()


if __name__=="__main__":raise SystemExit(main())
