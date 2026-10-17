"""Read-only host reconciliation; emits fixed metadata, never configuration secrets."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from urllib.error import HTTPError
from urllib.request import ProxyHandler,build_opener

from sqlalchemy import create_engine,text

from hope.api.operations_deployment import inspect_release,verify_database_role
from hope.api.release_attestation import _NoRedirect,verify_exact_ci
from hope.infrastructure.paper_service import verify_schema_compatibility

UNITS=("hope-paper.service","hope-ops-dashboard-web.service","hope-ops-dashboard-export.timer")


def _unit(unit):
    try:
        result=subprocess.run(["systemctl","show",unit,"--property=ActiveState,SubState,UnitFileState"],
            capture_output=True,text=True,timeout=10,check=True)
        fields=dict(line.split("=",1) for line in result.stdout.splitlines() if "=" in line)
        return {key:fields.get(key) if fields.get(key) in {
            "active","inactive","failed","activating","deactivating","running","dead","exited","waiting",
            "enabled","disabled","static","indirect","masked","enabled-runtime"} else "UNKNOWN"
            for key in ("ActiveState","SubState","UnitFileState")}
    except Exception:
        return {"status":"UNAVAILABLE"}


def _endpoint(path):
    try:
        with build_opener(ProxyHandler({}),_NoRedirect()).open("http://127.0.0.1:8788"+path,timeout=5) as response:
            body=response.read(32769)
            if len(body)>32768:raise ValueError("oversize")
            value=json.loads(body)
            if not isinstance(value,dict):raise ValueError("shape")
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
        return {"http_status":None,"status":"UNAVAILABLE"}


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
        endpoints={path:_endpoint(path) for path in ("/status.json","/snapshot.json","/operations.json","/readiness.json")},
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
