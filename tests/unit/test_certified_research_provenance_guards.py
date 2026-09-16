from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hope.application.experiments.research_runs import (
    CertifiedResearchInputLoader,
    research_run_fingerprint,
)
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.market_data.context import PITMarketContext
from hope.domain.universe.models import UniverseMember, UniverseVersion
from hope.infrastructure.repositories.experiments import ExperimentRecord


UTC = timezone.utc


def _experiment() -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id="exp-certified-provenance-guards",
        hypothesis="repository substitution must fail closed",
        strategy_version_id=uuid4(),
        dataset_version_id=uuid4(),
        universe_version_id=uuid4(),
        configuration_hash="a" * 64,
        environment="BACKTEST",
        status="CREATED",
    )


def _snapshot(
    experiment: ExperimentRecord,
    *,
    pit_certified: bool = True,
    universe_version_id=None,
) -> UniverseSnapshot:
    return UniverseSnapshot(
        universe_version_id=universe_version_id or experiment.universe_version_id,
        version=UniverseVersion(
            universe_id=uuid4(),
            version="v1",
            declared_member_count=1,
            pit_certified=pit_certified,
        ),
        members=(UniverseMember(instrument_id=uuid4()),),
    )


def test_research_run_fingerprint_rejects_non_pit_certified_universe() -> None:
    experiment = _experiment()
    with pytest.raises(ValueError, match="UNIVERSE_NOT_PIT_CERTIFIED"):
        research_run_fingerprint(
            experiment,
            as_of=datetime(2026, 1, 2, tzinfo=UTC),
            universe_snapshot=_snapshot(experiment, pit_certified=False),
        )


def test_input_loader_rejects_non_pit_certified_universe_before_market_read() -> None:
    experiment = _experiment()
    snapshot = _snapshot(experiment, pit_certified=False)

    class Universes:
        def get(self, universe_version_id):
            return snapshot

    class Contexts:
        def get(self, *args, **kwargs):
            raise AssertionError("market read must not occur")

    loader = CertifiedResearchInputLoader(Contexts(), Universes())
    with pytest.raises(ValueError, match="UNIVERSE_NOT_PIT_CERTIFIED"):
        loader.universe(experiment)


def test_input_loader_revalidates_snapshot_identity_before_market_read() -> None:
    experiment = _experiment()
    wrong_snapshot = _snapshot(experiment, universe_version_id=uuid4())

    class Contexts:
        def get(self, *args, **kwargs):
            raise AssertionError("market read must not occur")

    loader = CertifiedResearchInputLoader(Contexts(), object())
    with pytest.raises(ValueError, match="UNIVERSE_VERSION_MISMATCH"):
        loader.load(
            experiment,
            as_of=datetime(2026, 1, 2, tzinfo=UTC),
            universe_snapshot=wrong_snapshot,
        )


def test_input_loader_rejects_market_context_for_different_as_of() -> None:
    experiment = _experiment()
    snapshot = _snapshot(experiment)
    requested = datetime(2026, 1, 2, tzinfo=UTC)

    class Contexts:
        def get(self, dataset_version_id, *, as_of, universe_version_id, instrument_ids):
            return PITMarketContext(as_of=as_of + timedelta(seconds=1), bars=())

    loader = CertifiedResearchInputLoader(Contexts(), object())
    with pytest.raises(ValueError, match="MARKET_CONTEXT_AS_OF_MISMATCH"):
        loader.load(
            experiment,
            as_of=requested,
            universe_snapshot=snapshot,
        )
