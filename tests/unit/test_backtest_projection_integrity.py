from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from test_backtest_evidence import _result
from test_verified_research_source import _fixture, _resolver
from hope.application.backtests.engine import DeterministicBacktest
from hope.application.backtests.evidence import project_backtest_result, verify_backtest_result_projection
from hope.application.experiments.research_runs import research_result_fingerprint
from hope.domain.execution import CostModel, OrderSide
from hope.domain.market_data.models import MarketBar
from hope.domain.risk.models import RiskAssessment, RiskDecision
from hope.domain.signal.models import Signal, SignalType


@pytest.mark.parametrize("events", [False, True])
def test_complete_existing_projection_verifies_without_mutation(events):
    projected=project_backtest_result(_result(with_event_decision=events))
    original=deepcopy(projected)
    verify_backtest_result_projection(projected)
    assert projected==original


def test_real_engine_cost_inclusive_round_trip_verifies_nested_fills_and_audits():
    instrument=uuid4();start=datetime(2026,1,2,14,tzinfo=UTC)
    bars=tuple(MarketBar(instrument_id=str(instrument),event_time=start+timedelta(minutes=i),
        available_time=start+timedelta(minutes=i),ingestion_time=start+timedelta(minutes=i),
        open="100",high="101",low="99",close="100",volume="1000") for i in range(3))
    def strategy(context):
        if context.as_of not in (start,start+timedelta(minutes=1)): return None
        return Signal(signal_id=uuid4(),instrument_id=instrument,strategy_version="test-v1",
            decision_time=context.as_of,signal_type=SignalType.ENTRY if context.as_of==start else SignalType.EXIT,
            conviction="0.5",inputs_hash="a"*64)
    result=DeterministicBacktest(Decimal("1000"),CostModel("test-cost",Decimal("0.001"),Decimal("2"))).run(
        bars,strategy,lambda signal:RiskAssessment(signal_id=signal.signal_id,decision=RiskDecision.APPROVE,
            reason_code="CONTROLLED_TEST",approved_quantity=Decimal("1")),OrderSide.BUY)
    projected=project_backtest_result(result)
    assert any(event["result"]["fill"] is not None for event in projected["result"]["events"])
    verify_backtest_result_projection(projected)


@pytest.mark.parametrize("field", ["initial_cash","final_state","events","valuations","metrics","unfilled_order_ids","decisions"])
def test_missing_root_result_field_is_rejected(field):
    payload=project_backtest_result(_result(with_event_decision=True));del payload["result"][field]
    with pytest.raises(ValueError,match="BACKTEST_EVIDENCE_RESULT_"):
        verify_backtest_result_projection(payload)


@pytest.mark.parametrize("change", [
    lambda p:p.update(extra="private"),
    lambda p:p["result"].update(extra="private"),
    lambda p:p["result"]["metrics"].update(extra="private"),
    lambda p:p["result"].update(initial_cash=100),
    lambda p:p["result"].update(initial_cash="100.00"),
    lambda p:p["result"]["metrics"].update(sharpe="NaN"),
    lambda p:p["result"]["final_state"].update(cash="Infinity"),
    lambda p:p["result"]["valuations"][0].update(as_of="2026-01-02T14:00:00"),
    lambda p:p["result"]["events"][0]["bar"].update(high="1"),
    lambda p:p["result"]["decisions"][0]["signal"].update(signal_id="invalid"),
    lambda p:p["result"]["decisions"][0]["risk"].update(decision="INVENTED"),
    lambda p:p["result"]["events"][0]["result"].update(fill={"price":"100"}),
])
def test_noncanonical_or_invalid_nested_material_is_rejected(change):
    payload=project_backtest_result(_result(with_event_decision=True));change(payload)
    with pytest.raises(ValueError,match="BACKTEST_EVIDENCE_RESULT_"):
        verify_backtest_result_projection(payload)


@pytest.mark.parametrize("fragment", [None,{}, {"schema":"hope.backtest-result.v1"},
    {"schema":"hope.backtest-result.v1","result":{"metrics":{}}}])
def test_hash_matching_incomplete_certified_source_cannot_pass(fragment):
    run_id,experiment,run,evidence,plan,universe,markets=_fixture()
    evidence.canonical_result["backtest"]=fragment
    evidence.canonical_result,evidence.result_fingerprint=research_result_fingerprint(evidence.canonical_result)
    with pytest.raises(ValueError,match="BACKTEST.*(SCHEMA_MISMATCH|RESULT_)"):
        _resolver(experiment,run,evidence,plan,universe,markets).resolve(run_id)
