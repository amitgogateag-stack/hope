from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.experiments.execution import (
    CertifiedResearchExecutor,
    CertifiedResearchInputs,
    ResearchExecutionImplementation,
    ResearchExecutionRegistry,
)
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.market_data.context import PITMarketContext
from hope.domain.market_data.models import MarketBar
from hope.domain.universe.models import UniverseMember, UniverseVersion
from hope.infrastructure.repositories.execution_provenance import CertifiedExecutionPlan


UTC = timezone.utc


def _plan():
    return CertifiedExecutionPlan(
        experiment_id="exp-registry",
        strategy_version_id=uuid4(),
        strategy_id=uuid4(),
        strategy_version="1.2.3",
        code_commit="abc123",
        configuration_hash="a" * 64,
        configuration={"lookback": 20},
    )


def _snapshot(instrument_id=None):
    instrument_id = instrument_id or uuid4()
    return UniverseSnapshot(
        universe_version_id=uuid4(),
        version=UniverseVersion(
            universe_id=uuid4(),
            version="v1",
            declared_member_count=1,
            pit_certified=True,
        ),
        members=(UniverseMember(instrument_id=instrument_id),),
    )


def _bar(instrument_id: str):
    as_of = datetime(2026, 1, 2, tzinfo=UTC)
    return MarketBar(
        instrument_id=instrument_id,
        event_time=as_of,
        available_time=as_of,
        ingestion_time=as_of,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )


def _inputs():
    snapshot = _snapshot()
    return CertifiedResearchInputs(
        market_context=PITMarketContext(as_of=datetime(2026, 1, 2, tzinfo=UTC), bars=()),
        universe_snapshot=snapshot,
    )


def _implementation(plan, execute=lambda plan, inputs: (plan, inputs), **changes):
    values = {
        "strategy_version_id": plan.strategy_version_id,
        "strategy_id": plan.strategy_id,
        "strategy_version": plan.strategy_version,
        "code_commit": plan.code_commit,
        "execute": execute,
    }
    values.update(changes)
    return ResearchExecutionImplementation(**values)


def test_certified_executor_runs_only_exact_registered_implementation() -> None:
    plan = _plan()
    calls = []

    def execute(resolved_plan, inputs):
        calls.append((resolved_plan, inputs))
        return {"ok": True}

    executor = CertifiedResearchExecutor(
        ResearchExecutionRegistry([_implementation(plan, execute=execute)])
    )
    inputs = _inputs()
    assert executor.execute(plan, inputs) == {"ok": True}
    assert calls == [(plan, inputs)]


def test_registry_rejects_missing_and_duplicate_strategy_version_identity() -> None:
    plan = _plan()
    with pytest.raises(RuntimeError, match="RESEARCH_IMPLEMENTATION_NOT_REGISTERED"):
        CertifiedResearchExecutor(ResearchExecutionRegistry([])).execute(plan, _inputs())

    implementation = _implementation(plan)
    with pytest.raises(ValueError, match="DUPLICATE_STRATEGY_VERSION_ID"):
        ResearchExecutionRegistry([implementation, implementation])


def test_registry_fails_closed_on_strategy_or_commit_identity_mismatch() -> None:
    plan = _plan()
    cases = (
        ({"strategy_id": uuid4()}, "STRATEGY_ID_MISMATCH"),
        ({"strategy_version": "9.9.9"}, "STRATEGY_VERSION_MISMATCH"),
        ({"code_commit": "different"}, "CODE_COMMIT_MISMATCH"),
    )
    for changes, error in cases:
        executor = CertifiedResearchExecutor(
            ResearchExecutionRegistry([_implementation(plan, **changes)])
        )
        with pytest.raises(ValueError, match=error):
            executor.execute(plan, _inputs())


def test_registry_validates_canonical_definition_and_callable() -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="STRATEGY_VERSION_NOT_CANONICAL"):
        _implementation(plan, strategy_version=" 1.2.3")
    with pytest.raises(ValueError, match="CODE_COMMIT_NOT_CANONICAL"):
        _implementation(plan, code_commit=" abc123")
    with pytest.raises(TypeError, match="EXECUTOR_MUST_BE_CALLABLE"):
        _implementation(plan, execute=object())
    with pytest.raises(TypeError, match="RESEARCH_IMPLEMENTATION_REQUIRED"):
        ResearchExecutionRegistry([object()])


def test_certified_inputs_and_executor_reject_unsealed_inputs() -> None:
    plan = _plan()
    implementation = _implementation(plan)
    with pytest.raises(TypeError, match="REQUIRES_REGISTRY"):
        CertifiedResearchExecutor(object())
    executor = CertifiedResearchExecutor(ResearchExecutionRegistry([implementation]))
    with pytest.raises(TypeError, match="REQUIRES_EXECUTION_PLAN"):
        executor.execute(object(), _inputs())
    with pytest.raises(TypeError, match="REQUIRES_CERTIFIED_INPUTS"):
        executor.execute(plan, object())
    with pytest.raises(TypeError, match="REQUIRE_MARKET_CONTEXT"):
        CertifiedResearchInputs(object(), _inputs().universe_snapshot)
    with pytest.raises(TypeError, match="REQUIRE_UNIVERSE_SNAPSHOT"):
        CertifiedResearchInputs(_inputs().market_context, object())


def test_certified_inputs_reject_market_bar_outside_frozen_universe() -> None:
    frozen_instrument = uuid4()
    snapshot = _snapshot(frozen_instrument)
    context = PITMarketContext(
        as_of=datetime(2026, 1, 2, tzinfo=UTC),
        bars=(_bar(str(uuid4())),),
    )
    with pytest.raises(ValueError, match="BAR_OUTSIDE_ACTIVE_FROZEN_UNIVERSE"):
        CertifiedResearchInputs(context, snapshot)


def test_certified_inputs_accept_market_bar_inside_frozen_universe() -> None:
    frozen_instrument = uuid4()
    snapshot = _snapshot(frozen_instrument)
    context = PITMarketContext(
        as_of=datetime(2026, 1, 2, tzinfo=UTC),
        bars=(_bar(str(frozen_instrument)),),
    )
    inputs = CertifiedResearchInputs(context, snapshot)
    assert inputs.market_context == context
    assert inputs.universe_snapshot == snapshot
