from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from hope.application.experiments.execution import CertifiedResearchInputs
from hope.application.experiments.research_runs import CertifiedResearchInputLoader
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.market_data.context import PITMarketContext
from hope.domain.market_data.models import MarketBar
from hope.domain.universe.models import UniverseMember, UniverseVersion
from hope.infrastructure.repositories.experiments import ExperimentRecord


UTC = timezone.utc


def _snapshot(members):
    members = tuple(members)
    return UniverseSnapshot(
        universe_version_id=uuid4(),
        version=UniverseVersion(
            universe_id=uuid4(),
            version="v1",
            declared_member_count=len(members),
            pit_certified=True,
        ),
        members=members,
    )


def _bar(instrument_id, as_of):
    return MarketBar(
        instrument_id=str(instrument_id),
        event_time=as_of,
        available_time=as_of,
        ingestion_time=as_of,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )


def test_snapshot_active_members_use_half_open_validity_interval() -> None:
    t0 = datetime(2026, 1, 2, tzinfo=UTC)
    starts_now = UniverseMember(instrument_id=uuid4(), valid_from=t0)
    ends_now = UniverseMember(instrument_id=uuid4(), valid_to=t0)
    always = UniverseMember(instrument_id=uuid4())
    snapshot = _snapshot((starts_now, ends_now, always))

    assert set(snapshot.active_instrument_ids(t0)) == {starts_now.instrument_id, always.instrument_id}
    assert ends_now.instrument_id in snapshot.active_instrument_ids(t0 - timedelta(microseconds=1))


def test_snapshot_active_members_require_aware_as_of() -> None:
    snapshot = _snapshot((UniverseMember(instrument_id=uuid4()),))
    with pytest.raises(ValueError, match="UNIVERSE_SNAPSHOT_AS_OF_MUST_BE_TIMEZONE_AWARE"):
        snapshot.active_members(datetime(2026, 1, 2))


def test_certified_inputs_reject_bar_for_inactive_snapshot_member() -> None:
    t0 = datetime(2026, 1, 2, tzinfo=UTC)
    inactive = UniverseMember(instrument_id=uuid4(), valid_from=t0 + timedelta(days=1))
    snapshot = _snapshot((inactive,))
    context = PITMarketContext(as_of=t0, bars=(_bar(inactive.instrument_id, t0),))

    with pytest.raises(ValueError, match="BAR_OUTSIDE_ACTIVE_FROZEN_UNIVERSE"):
        CertifiedResearchInputs(context, snapshot)


def test_input_loader_requests_only_members_active_at_as_of() -> None:
    t0 = datetime(2026, 1, 2, tzinfo=UTC)
    active = UniverseMember(instrument_id=uuid4(), valid_from=t0)
    future = UniverseMember(instrument_id=uuid4(), valid_from=t0 + timedelta(days=1))
    expired = UniverseMember(instrument_id=uuid4(), valid_to=t0)
    snapshot = _snapshot((active, future, expired))
    experiment = ExperimentRecord(
        experiment_id="exp-temporal-universe",
        hypothesis="temporal membership",
        strategy_version_id=uuid4(),
        dataset_version_id=uuid4(),
        universe_version_id=snapshot.universe_version_id,
        configuration_hash="a" * 64,
        environment="BACKTEST",
        status="CREATED",
    )
    calls = []

    class Contexts:
        def get(self, dataset_version_id, *, as_of, universe_version_id, instrument_ids):
            calls.append((dataset_version_id, as_of, universe_version_id, instrument_ids))
            return PITMarketContext(as_of=as_of, bars=())

    class Universes:
        def get(self, universe_version_id):
            return snapshot

    loader = CertifiedResearchInputLoader(Contexts(), Universes())
    resolved = loader.universe(experiment)
    loader.load(experiment, as_of=t0, universe_snapshot=resolved)

    assert calls == [(
        experiment.dataset_version_id,
        t0,
        experiment.universe_version_id,
        (active.instrument_id,),
    )]
