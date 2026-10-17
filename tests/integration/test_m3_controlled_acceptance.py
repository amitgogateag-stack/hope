"""Synthetic controlled acceptance on genuine PostgreSQL with unprivileged writer."""
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine,event,text

from test_certified_paper_bridge import certified_case
from hope.application.jobs import create_scheduled_job_run
from hope.application.market_data.calendar import MarketSessionCalendar
from hope.infrastructure.paper_acceptance import controlled_paper_acceptance
from hope.infrastructure.paper_service import PaperServicePlan
from hope.infrastructure.scheduling.paper import OperationalPaperJobBinding,OperationalPaperSchedule


@pytest.fixture
def acceptance_case(certified_case,monkeypatch,tmp_path):
    owner,make=certified_case
    name="m3_test_"+uuid4().hex
    with owner.begin() as connection:
        connection.execute(text(f'CREATE ROLE "{name}" NOLOGIN'))
        connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{name}"'))
        connection.execute(text(f'GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO "{name}"'))
        connection.execute(text(f'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO "{name}"'))
    writer=create_engine(owner.url)
    @event.listens_for(writer,"connect")
    def assume_role(connection,record):
        with connection.cursor() as cursor:cursor.execute(f'SET ROLE "{name}"')
        connection.commit()
    monkeypatch.setenv("HOPE_DEPLOYED_COMMIT","a"*40)
    def build(path="NO_TRADE",sessions=1):
        data,_,strategy,work,_=make(emit=path!="NO_TRADE",blocked=path=="RISK_BLOCKED")
        binding=OperationalPaperJobBinding(data["strategy_version_id"],data["market"],"m3-acceptance",work)
        run=create_scheduled_job_run(binding.durable_job_key,data["as_of"])
        strategy.run=run
        calendar=MarketSessionCalendar(tuple((data["as_of"]+timedelta(hours=2*i),data["as_of"]+timedelta(hours=2*i+1)) for i in range(sessions)))
        factory=lambda connection,current:PaperServicePlan((binding,),
            (OperationalPaperSchedule(data["strategy_version_id"],data["market"],"m3-acceptance",timedelta(0)),),
            {data["market"]:calendar},data["as_of"],data["as_of"]+timedelta(hours=2*sessions-1),timedelta(minutes=2))
        arguments=dict(expected_database=owner.url.database,expected_sha="a"*40,run_id=run.job_run_id,
            migrations_dir=Path(__file__).parents[2]/"migrations",evidence_dir=tmp_path/uuid4().hex,
            attestation={"sha":"a"*40,"test_fixture":True},now=lambda:data["as_of"]+timedelta(minutes=1))
        return data,run,strategy,factory,arguments
    try:yield owner,writer,build
    finally:
        writer.dispose()
        with owner.begin() as connection:
            connection.execute(text(f'DROP OWNED BY "{name}"'))
            connection.execute(text(f'DROP ROLE "{name}"'))


@pytest.mark.integration
@pytest.mark.parametrize("path",["NO_TRADE","FILLED","RISK_BLOCKED"])
def test_bounded_acceptance_restart_and_authenticated_reconciliation(acceptance_case,path):
    owner,writer,build=acceptance_case
    data,run,strategy,factory,args=build(path)
    result=controlled_paper_acceptance(writer,factory,**args)
    assert result["outcome"]==path and result["worker_state"]=="STOPPED"
    assert result["reconciliation"]=="AUTHENTICATED" and result["dashboard_review"]=="PENDING"
    assert len(strategy.calls)==1
    with owner.connect() as connection:
        assert connection.execute(text("SELECT status FROM job_runs WHERE job_run_id=:id"),{"id":run.job_run_id}).scalar_one()=="SUCCEEDED"
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),{"id":run.job_run_id}).scalar_one()==(5 if path=="FILLED" else 1)
        assert connection.execute(text("SELECT count(*) FROM audit_events WHERE event_type='PAPER_RUN_RECONCILED' AND entity_id=:id"),{"id":str(run.job_run_id)}).scalar_one()==1
    assert (args["evidence_dir"]/"acceptance.json").is_file()
    with pytest.raises(RuntimeError,match="FRESH_ACCEPTANCE_RUN_REQUIRED"):
        controlled_paper_acceptance(writer,factory,**{**args,"evidence_dir":args["evidence_dir"].with_name("retry")})
    assert len(strategy.calls)==1


@pytest.mark.integration
@pytest.mark.parametrize("failure",["database","role","selection","multiple","commit"])
def test_unsafe_acceptance_has_no_claims_or_strategy_calls(acceptance_case,failure,monkeypatch):
    owner,writer,build=acceptance_case
    data,run,strategy,factory,args=build(sessions=2 if failure=="multiple" else 1)
    if failure=="database":args["expected_database"]="wrong_database"
    if failure=="selection":args["run_id"]=uuid4()
    if failure=="commit":monkeypatch.setenv("HOPE_DEPLOYED_COMMIT","b"*40)
    with pytest.raises((RuntimeError,ValueError)):
        controlled_paper_acceptance(owner if failure=="role" else writer,factory,**args)
    assert strategy.calls==[]
    with owner.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM job_runs WHERE job_run_id=:id"),{"id":run.job_run_id}).scalar_one()==0
    assert not (args["evidence_dir"]/"acceptance.json").exists()


@pytest.mark.integration
def test_postcommit_proof_failure_does_not_claim_rollback_or_publish_acceptance(acceptance_case,monkeypatch):
    from hope.infrastructure import paper_acceptance as module
    owner,writer,build=acceptance_case
    data,run,strategy,factory,args=build()
    monkeypatch.setattr(module,"recover_certified_paper_batch",lambda *a,**k:(_ for _ in ()).throw(RuntimeError("injected proof failure")))
    with pytest.raises(RuntimeError,match="proof failure"):
        controlled_paper_acceptance(writer,factory,**args)
    assert not (args["evidence_dir"]/"acceptance.json").exists()
    assert len(strategy.calls)==1
    with owner.connect() as connection:
        assert connection.execute(text("SELECT status FROM job_runs WHERE job_run_id=:id"),{"id":run.job_run_id}).scalar_one()=="SUCCEEDED"


@pytest.mark.integration
@pytest.mark.parametrize("path",["NO_TRADE","FILLED"])
def test_competing_acceptance_invocations_never_create_two_executions(acceptance_case,path):
    owner,writer,build=acceptance_case
    data,run,strategy,factory,args=build(path)
    barrier=Barrier(2)
    def execute(index):
        barrier.wait(timeout=5)
        try:
            return controlled_paper_acceptance(writer,factory,**{**args,
                "evidence_dir":args["evidence_dir"].with_name(f"contender-{index}")})["status"]
        except RuntimeError:
            return "BLOCKED"
    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(execute,(0,1)))
    assert results.count("BOUNDED_PAPER_VERIFIED")==1 and results.count("BLOCKED")==1
    assert len(strategy.calls)==1
    with owner.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM paper_effects WHERE job_run_id=:id"),{"id":run.job_run_id}).scalar_one()==(5 if path=="FILLED" else 1)
