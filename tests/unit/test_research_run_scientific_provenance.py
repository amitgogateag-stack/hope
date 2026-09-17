from datetime import datetime, timezone
from uuid import uuid4

from hope.application.experiments.config_hash import configuration_hash
from hope.application.experiments.research_runs import (
    research_run_fingerprint,
    research_run_provenance,
)
from hope.application.universe.snapshot import UniverseSnapshot
from hope.domain.universe.models import UniverseMember, UniverseVersion
from hope.infrastructure.repositories.experiments import ExperimentRecord


UTC = timezone.utc
MANIFEST_HASH = "c" * 64


def _fixture():
    configuration = {"strategy": {"lookback": 20}}
    experiment = ExperimentRecord(
        experiment_id="exp-run-provenance",
        hypothesis="Bind exact scientific identity",
        strategy_version_id=uuid4(),
        dataset_version_id=uuid4(),
        universe_version_id=uuid4(),
        configuration_hash=configuration_hash(configuration),
        environment="BACKTEST",
        status="CREATED",
    )
    members = (UniverseMember(instrument_id=uuid4()), UniverseMember(instrument_id=uuid4()))
    snapshot = UniverseSnapshot(
        universe_version_id=experiment.universe_version_id,
        version=UniverseVersion(
            universe_id=uuid4(),
            version="v1",
            declared_member_count=len(members),
            pit_certified=True,
        ),
        members=members,
    )
    return experiment, snapshot


def test_research_run_provenance_is_self_describing_and_matches_fingerprint() -> None:
    experiment, snapshot = _fixture()
    as_of = datetime(2026, 1, 2, tzinfo=UTC)
    provenance = research_run_provenance(
        experiment,
        as_of=as_of,
        universe_snapshot=snapshot,
        market_data_manifest_hash=MANIFEST_HASH,
    )
    assert provenance == {
        "experiment_id": experiment.experiment_id,
        "strategy_version_id": str(experiment.strategy_version_id),
        "dataset_version_id": str(experiment.dataset_version_id),
        "universe_version_id": str(experiment.universe_version_id),
        "universe_membership_hash": snapshot.membership_hash,
        "market_data_manifest_hash": MANIFEST_HASH,
        "configuration_hash": experiment.configuration_hash,
        "environment": experiment.environment,
        "as_of": as_of.isoformat(),
        "run_fingerprint": research_run_fingerprint(
            experiment,
            as_of=as_of,
            universe_snapshot=snapshot,
        ),
    }


def test_research_run_provenance_changes_with_scientific_inputs() -> None:
    experiment, snapshot = _fixture()
    first = research_run_provenance(
        experiment,
        as_of=datetime(2026, 1, 2, tzinfo=UTC),
        universe_snapshot=snapshot,
        market_data_manifest_hash=MANIFEST_HASH,
    )
    later = research_run_provenance(
        experiment,
        as_of=datetime(2026, 1, 3, tzinfo=UTC),
        universe_snapshot=snapshot,
        market_data_manifest_hash=MANIFEST_HASH,
    )
    different_manifest = research_run_provenance(
        experiment,
        as_of=datetime(2026, 1, 2, tzinfo=UTC),
        universe_snapshot=snapshot,
        market_data_manifest_hash="d" * 64,
    )
    assert first["run_fingerprint"] != later["run_fingerprint"]
    assert first["run_fingerprint"] != different_manifest["run_fingerprint"]
    assert first["as_of"] != later["as_of"]
