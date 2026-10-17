"""Counterfeit nested results retain matching hashes and all PostgreSQL guards."""
from datetime import timedelta
import os
from pathlib import Path
import runpy

import pytest
from sqlalchemy import create_engine,text

from test_certified_paper_bridge import configuration
from hope.application.jobs import create_scheduled_job_run
from hope.domain.strategy.candidates import StrategyCandidateClassification,StrategyCandidateState
from hope.infrastructure.certified_paper import CertifiedPaperDefinition,CertifiedResearchPaperWork
from hope.infrastructure.paper_runtime import PaperJobDefinition,PaperJobRegistry,run_paper_once
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.strategy_candidates import SqlAlchemyStrategyCandidateRepository

seed=runpy.run_path(str(Path(__file__).with_name("m2_research_fixture.py")))["seed"]


@pytest.mark.integration
@pytest.mark.parametrize("fragment", [
    {"schema":"hope.backtest-result.v1"},
    {"schema":"hope.backtest-result.v1","result":{"metrics":{}}},
    {"schema":"hope.backtest-result.v1","result":"invented"},
])
def test_stored_hash_matching_malformed_result_rejected_before_factory_and_claim(fragment):
    url=os.getenv("HOPE_DATABASE_URL")
    if not url: pytest.skip("HOPE_DATABASE_URL is not configured")
    engine=create_engine(url)
    with engine.begin() as connection:
        apply_migrations(connection,Path(__file__).parents[2]/"migrations")
        data=seed(connection,configuration=configuration(),backtest_override=fragment)
    calls=[]
    def forbidden_factory(plan):
        calls.append(True)
        raise AssertionError("Malformed research reached implementation factory")
    run=create_scheduled_job_run(f"paper:USA:{data['strategy_version_id']}:malformed-evidence",data["as_of"])
    work=CertifiedResearchPaperWork(data["strategy_version_id"],data["market"],data["classification_id"],
        data["research_decision_id"],data["dataset_version_id"],
        CertifiedPaperDefinition(data["family"],data["code_commit"],forbidden_factory,forbidden_factory))
    try:
        with pytest.raises(ValueError,match="BACKTEST_EVIDENCE_RESULT_"):
            run_paper_once(engine,run,PaperJobRegistry([PaperJobDefinition(run.job_key,work)]),
                now=lambda:data["as_of"]+timedelta(minutes=1))
        assert calls==[]
        with engine.connect() as connection:
            for table in ("job_runs","paper_strategy_authorizations","paper_effects","paper_certified_outcomes"):
                assert connection.execute(text(f"SELECT count(*) FROM {table} WHERE job_run_id=:id"),{"id":run.job_run_id}).scalar_one()==0
            assert connection.execute(text("SELECT count(*) FROM paper_portfolios WHERE portfolio_id=:id"),
                {"id":data["configuration"]["paper_execution"]["portfolio_id"]}).scalar_one()==0
    finally:
        with engine.begin() as connection:
            SqlAlchemyStrategyCandidateRepository(connection).append(StrategyCandidateClassification(
                strategy_version_id=data["strategy_version_id"],markets=frozenset({data["market"]}),
                state=StrategyCandidateState.RESEARCH,rationale="Counterfeit negative evidence fixture cleanup"))
        engine.dispose()
