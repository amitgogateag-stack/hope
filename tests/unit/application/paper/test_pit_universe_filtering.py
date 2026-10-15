from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hope.application.paper.jobs import PaperStrategyDecisionJob
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.market_data.context import PITMarketContext
from hope.domain.market_data.models import MarketBar
from hope.domain.strategy.models import ParameterSnapshot, Strategy
from hope.domain.universe.models import UniverseMember, UniverseVersion
from hope.infrastructure.paper_runtime import (
    DurableUniversePaperStrategyDecision,
    PaperMarketDataFreshnessPolicy,
)


UTC = timezone.utc


class _Parameters(ParameterSnapshot):
    pass


class _CapturingStrategy(Strategy):
    name = "pit-active-members"
    version = "v1"

    def __init__(self) -> None:
        self.calls = []

    def generate_signals(
        self,
        market_context,
        universe,
        universe_members,
        parameters,
        inputs_hash,
    ):
        self.calls.append(
            (market_context, universe, universe_members, parameters, inputs_hash)
        )
        return ()


def test_durable_decision_exposes_only_active_members_to_context_and_strategy(
    monkeypatch,
) -> None:
    as_of = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
    active_id = uuid4()
    expired_id = uuid4()
    snapshot = UniverseSnapshot(
        universe_version_id=uuid4(),
        version=UniverseVersion(
            universe_id=uuid4(),
            version="v1",
            declared_member_count=2,
            pit_certified=True,
        ),
        members=(
            UniverseMember(instrument_id=active_id),
            UniverseMember(
                instrument_id=expired_id,
                valid_to=as_of - timedelta(microseconds=1),
            ),
        ),
    )
    fresh_time = as_of - timedelta(minutes=1)
    context = PITMarketContext(
        as_of=as_of,
        bars=(
            MarketBar(
                instrument_id=str(active_id),
                event_time=fresh_time,
                available_time=fresh_time,
                effective_time=fresh_time,
                ingestion_time=fresh_time,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1"),
            ),
        ),
    )
    requested = []

    class FakeSnapshots:
        def __init__(self, connection):
            pass

        def get(self, universe_version_id):
            assert universe_version_id == snapshot.universe_version_id
            return snapshot

    class FakeContexts:
        def __init__(self, connection):
            pass

        def get(
            self,
            dataset_version_id,
            *,
            as_of,
            universe_version_id,
            instrument_ids,
        ):
            requested.append(instrument_ids)
            return context

    monkeypatch.setattr(
        "hope.infrastructure.paper_runtime.UniverseSnapshotRepository",
        FakeSnapshots,
    )
    monkeypatch.setattr(
        "hope.infrastructure.paper_runtime.PITMarketContextRepository",
        FakeContexts,
    )

    strategy = _CapturingStrategy()
    decision = DurableUniversePaperStrategyDecision(
        strategy=strategy,
        dataset_version_id=uuid4(),
        as_of=as_of,
        universe_version_id=snapshot.universe_version_id,
        parameters=_Parameters(),
        freshness_policy=PaperMarketDataFreshnessPolicy(timedelta(minutes=5)),
    )
    work = decision.bind(object())

    assert isinstance(work, PaperStrategyDecisionJob)
    assert work.market_data_max_age == timedelta(minutes=5)
    assert requested == [(active_id,)]
    runtime = SimpleNamespace(
        cycle=SimpleNamespace(
            job_run=SimpleNamespace(scheduled_for=as_of),
        ),
        record_signal=lambda signal: None,
        record_no_trade=lambda **outcome: None,
    )
    work(runtime)

    assert len(strategy.calls) == 1
    assert strategy.calls[0][2] == (
        UniverseMember(instrument_id=active_id),
    )
    assert all(
        member.instrument_id != expired_id for member in strategy.calls[0][2]
    )



@pytest.mark.parametrize(
    ("violation", "error_code"),
    (
        ("as_of", "PAPER_MARKET_CONTEXT_AS_OF_MISMATCH"),
        (
            "instrument",
            "PAPER_MARKET_CONTEXT_INSTRUMENT_OUTSIDE_ACTIVE_UNIVERSE",
        ),
    ),
)
def test_durable_decision_authenticates_repository_context_identity(
    monkeypatch,
    violation: str,
    error_code: str,
) -> None:
    as_of = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
    active_id = uuid4()
    unrequested_id = uuid4()
    snapshot = UniverseSnapshot(
        universe_version_id=uuid4(),
        version=UniverseVersion(
            universe_id=uuid4(),
            version="v1",
            declared_member_count=1,
            pit_certified=True,
        ),
        members=(UniverseMember(instrument_id=active_id),),
    )
    context_as_of = (
        as_of + timedelta(microseconds=1)
        if violation == "as_of"
        else as_of
    )
    bar_time = as_of - timedelta(minutes=1)
    context = PITMarketContext(
        as_of=context_as_of,
        bars=(
            MarketBar(
                instrument_id=str(
                    unrequested_id if violation == "instrument" else active_id
                ),
                event_time=bar_time,
                available_time=bar_time,
                effective_time=bar_time,
                ingestion_time=bar_time,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1"),
            ),
        ),
    )

    class FakeSnapshots:
        def __init__(self, connection):
            pass

        def get(self, universe_version_id):
            assert universe_version_id == snapshot.universe_version_id
            return snapshot

    class FakeContexts:
        def __init__(self, connection):
            pass

        def get(
            self,
            dataset_version_id,
            *,
            as_of,
            universe_version_id,
            instrument_ids,
        ):
            assert instrument_ids == (active_id,)
            return context

    monkeypatch.setattr(
        "hope.infrastructure.paper_runtime.UniverseSnapshotRepository",
        FakeSnapshots,
    )
    monkeypatch.setattr(
        "hope.infrastructure.paper_runtime.PITMarketContextRepository",
        FakeContexts,
    )
    decision = DurableUniversePaperStrategyDecision(
        strategy=_CapturingStrategy(),
        dataset_version_id=uuid4(),
        as_of=as_of,
        universe_version_id=snapshot.universe_version_id,
        parameters=_Parameters(),
        freshness_policy=PaperMarketDataFreshnessPolicy(timedelta(minutes=5)),
    )

    with pytest.raises(RuntimeError, match=error_code):
        decision.bind(object())
