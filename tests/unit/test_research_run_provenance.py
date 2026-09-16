from datetime import datetime, timezone
from uuid import uuid4

import pytest

from hope.application.experiments.research_runs import CertifiedResearchContextLoader, research_run_fingerprint
from hope.infrastructure.repositories.experiments import ExperimentRecord


def _experiment() -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id="exp-certified-run",
        hypothesis="Certified evidence only",
        strategy_version_id=uuid4(),
        dataset_version_id=uuid4(),
        universe_version_id=uuid4(),
        configuration_hash="a" * 64,
        environment="BACKTEST",
        status="CREATED",
    )


def test_fingerprint_binds_exact_experiment_and_evidence_request() -> None:
    experiment = _experiment()
    t0 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    instruments = (uuid4(), uuid4())

    first = research_run_fingerprint(experiment, as_of=t0, instrument_ids=instruments)
    reordered = research_run_fingerprint(experiment, as_of=t0, instrument_ids=tuple(reversed(instruments)))
    later = research_run_fingerprint(experiment, as_of=datetime(2026, 1, 3, tzinfo=timezone.utc), instrument_ids=instruments)

    assert first == reordered
    assert first != later
    assert len(first) == 64


def test_fingerprint_rejects_duplicate_instruments() -> None:
    instrument_id = uuid4()
    with pytest.raises(ValueError, match="DUPLICATE_INSTRUMENT"):
        research_run_fingerprint(
            _experiment(),
            as_of=datetime(2026, 1, 2, tzinfo=timezone.utc),
            instrument_ids=(instrument_id, instrument_id),
        )


def test_certified_loader_derives_dataset_and_universe_from_experiment() -> None:
    experiment = _experiment()
    t0 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    instruments = (uuid4(),)

    class Experiments:
        def get(self, experiment_id):
            assert experiment_id == experiment.experiment_id
            return experiment

    class Contexts:
        def get(self, dataset_version_id, *, as_of, universe_version_id, instrument_ids):
            assert dataset_version_id == experiment.dataset_version_id
            assert universe_version_id == experiment.universe_version_id
            assert as_of == t0
            assert instrument_ids == instruments
            return "certified-context"

    loader = CertifiedResearchContextLoader(Experiments(), Contexts())
    assert loader.load(experiment.experiment_id, as_of=t0, instrument_ids=instruments) == "certified-context"


def test_certified_loader_rejects_unknown_experiment_before_market_read() -> None:
    class Experiments:
        def get(self, experiment_id):
            return None

    class Contexts:
        def get(self, *args, **kwargs):
            raise AssertionError("market read must not occur")

    loader = CertifiedResearchContextLoader(Experiments(), Contexts())
    with pytest.raises(KeyError, match="unknown experiment"):
        loader.load("missing", as_of=datetime(2026, 1, 2, tzinfo=timezone.utc), instrument_ids=())
