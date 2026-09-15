import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.dataset_versions import SqlAlchemyMarketDataVersionSealer
from hope.infrastructure.repositories.market_contexts import PITMarketContextRepository


@pytest.mark.integration
def test_legacy_market_data_version_sealer_cannot_bypass_manifest() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"
    with engine.begin() as connection:
        apply_migrations(connection, migrations_dir)

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            instrument_id = uuid4()
            alternate_instrument_id = uuid4()
            outsider_instrument_id = uuid4()
            pit_dataset_id = uuid4()
            non_pit_dataset_id = uuid4()
            good_version_id = uuid4()
            empty_version_id = uuid4()
            non_pit_version_id = uuid4()
            fake_sealed_empty_version_id = uuid4()
            fake_sealed_non_pit_version_id = uuid4()
            membership_bypass_version_id = uuid4()
            duplicate_manifest_version_id = uuid4()
            identity_conflict_version_id = uuid4()
            noncanonical_identity_version_id = uuid4()
            universe_id = uuid4()
            universe_version_id = uuid4()
            t0 = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": instrument_id, "symbol": f"SEAL-{instrument_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {
                    "instrument_id": alternate_instrument_id,
                    "symbol": f"ALTERNATE-{alternate_instrument_id}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {
                    "instrument_id": outsider_instrument_id,
                    "symbol": f"OUTSIDER-{outsider_instrument_id}",
                },
            )
            connection.execute(
                text("INSERT INTO universes(universe_id, name) VALUES (:id, :name)"),
                {"id": universe_id, "name": f"seal-{universe_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_versions("
                    "universe_version_id, universe_id, version, pit_certified, declared_member_count"
                    ") VALUES (:version_id, :universe_id, 'v1', TRUE, 2)"
                ),
                {"version_id": universe_version_id, "universe_id": universe_id},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_members(universe_version_id, instrument_id) "
                    "VALUES (:version_id, :instrument_id), (:version_id, :alternate_instrument_id)"
                ),
                {
                    "version_id": universe_version_id,
                    "instrument_id": instrument_id,
                    "alternate_instrument_id": alternate_instrument_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO datasets(dataset_id, name, source, pit_certified) VALUES "
                    "(:pit_id, :pit_name, 'TEST', TRUE), "
                    "(:non_pit_id, :non_pit_name, 'TEST', FALSE)"
                ),
                {
                    "pit_id": pit_dataset_id,
                    "pit_name": f"pit-{pit_dataset_id}",
                    "non_pit_id": non_pit_dataset_id,
                    "non_pit_name": f"non-pit-{non_pit_dataset_id}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions(dataset_version_id, dataset_id, version, vintage_label, immutable) VALUES "
                    "(:good, :pit_id, 'good', 'staging', FALSE), "
                    "(:empty, :pit_id, 'empty', 'staging', FALSE), "
                    "(:non_pit, :non_pit_id, 'non-pit', 'staging', FALSE), "
                    "(:membership_bypass, :pit_id, 'membership-bypass', 'staging', FALSE), "
                    "(:duplicate_manifest, :pit_id, 'duplicate-manifest', 'staging', FALSE), "
                    "(:identity_conflict, :pit_id, 'identity-conflict', 'staging', FALSE), "
                    "(:noncanonical_identity, :pit_id, 'noncanonical-identity', 'staging', FALSE), "
                    "(:fake_empty, :pit_id, 'fake-empty', 'sealed', TRUE), "
                    "(:fake_non_pit, :non_pit_id, 'fake-non-pit', 'sealed', TRUE)"
                ),
                {
                    "good": good_version_id,
                    "empty": empty_version_id,
                    "non_pit": non_pit_version_id,
                    "membership_bypass": membership_bypass_version_id,
                    "duplicate_manifest": duplicate_manifest_version_id,
                    "identity_conflict": identity_conflict_version_id,
                    "noncanonical_identity": noncanonical_identity_version_id,
                    "fake_empty": fake_sealed_empty_version_id,
                    "fake_non_pit": fake_sealed_non_pit_version_id,
                    "pit_id": pit_dataset_id,
                    "non_pit_id": non_pit_dataset_id,
                },
            )
            manifest = {
                "version": 2,
                "universe_version_id": str(universe_version_id),
                "windows": [
                    {
                        "source": "TEST",
                        "start": t0.isoformat(),
                        "end": (t0 + timedelta(minutes=1)).isoformat(),
                        "interval_seconds": 60,
                        "instruments": [
                            {
                                "source_symbol": "OUTSIDER",
                                "instrument_id": str(outsider_instrument_id),
                            }
                        ],
                    }
                ],
            }
            canonical_manifest = json.dumps(
                manifest, sort_keys=True, separators=(",", ":")
            )
            connection.execute(
                text(
                    "INSERT INTO market_data_coverage_manifests("
                    "dataset_version_id, universe_version_id, manifest_hash, manifest"
                    ") VALUES ("
                    ":version_id, :universe_version_id, :manifest_hash, CAST(:manifest AS JSONB)"
                    ")"
                ),
                {
                    "version_id": membership_bypass_version_id,
                    "universe_version_id": universe_version_id,
                    "manifest_hash": hashlib.sha256(
                        canonical_manifest.encode("utf-8")
                    ).hexdigest(),
                    "manifest": canonical_manifest,
                },
            )
            noncanonical_identity_manifest = {
                "version": 2,
                "universe_version_id": str(universe_version_id),
                "windows": [
                    {
                        "source": "TEST",
                        "start": t0.isoformat(),
                        "end": (t0 + timedelta(minutes=1)).isoformat(),
                        "interval_seconds": 60,
                        "instruments": [
                            {
                                "source_symbol": " SEAL ",
                                "instrument_id": str(instrument_id),
                            }
                        ],
                    }
                ],
            }
            noncanonical_identity_canonical = json.dumps(
                noncanonical_identity_manifest,
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                text(
                    "INSERT INTO market_data_coverage_manifests("
                    "dataset_version_id, universe_version_id, manifest_hash, manifest"
                    ") VALUES ("
                    ":version_id, :universe_version_id, :manifest_hash, CAST(:manifest AS JSONB)"
                    ")"
                ),
                {
                    "version_id": noncanonical_identity_version_id,
                    "universe_version_id": universe_version_id,
                    "manifest_hash": hashlib.sha256(
                        noncanonical_identity_canonical.encode("utf-8")
                    ).hexdigest(),
                    "manifest": noncanonical_identity_canonical,
                },
            )
            identity_conflict_manifest = {
                "version": 2,
                "universe_version_id": str(universe_version_id),
                "windows": [
                    {
                        "source": "TEST",
                        "start": t0.isoformat(),
                        "end": (t0 + timedelta(minutes=1)).isoformat(),
                        "interval_seconds": 60,
                        "instruments": [
                            {
                                "source_symbol": "SAME",
                                "instrument_id": str(instrument_id),
                            }
                        ],
                    },
                    {
                        "source": "TEST",
                        "start": (t0 + timedelta(minutes=1)).isoformat(),
                        "end": (t0 + timedelta(minutes=2)).isoformat(),
                        "interval_seconds": 60,
                        "instruments": [
                            {
                                "source_symbol": "SAME",
                                "instrument_id": str(alternate_instrument_id),
                            }
                        ],
                    },
                ],
            }
            identity_conflict_canonical = json.dumps(
                identity_conflict_manifest, sort_keys=True, separators=(",", ":")
            )
            connection.execute(
                text(
                    "INSERT INTO market_data_coverage_manifests("
                    "dataset_version_id, universe_version_id, manifest_hash, manifest"
                    ") VALUES ("
                    ":version_id, :universe_version_id, :manifest_hash, CAST(:manifest AS JSONB)"
                    ")"
                ),
                {
                    "version_id": identity_conflict_version_id,
                    "universe_version_id": universe_version_id,
                    "manifest_hash": hashlib.sha256(
                        identity_conflict_canonical.encode("utf-8")
                    ).hexdigest(),
                    "manifest": identity_conflict_canonical,
                },
            )
            duplicate_manifest = {
                "version": 2,
                "universe_version_id": str(universe_version_id),
                "windows": [
                    {
                        "source": "TEST",
                        "start": t0.isoformat(),
                        "end": (t0 + timedelta(minutes=1)).isoformat(),
                        "interval_seconds": 60,
                        "instruments": [
                            {
                                "source_symbol": "SEAL",
                                "instrument_id": str(instrument_id),
                            }
                        ],
                    },
                    {
                        "source": "TEST",
                        "start": t0.isoformat(),
                        "end": (t0 + timedelta(minutes=1)).isoformat(),
                        "interval_seconds": 60,
                        "instruments": [
                            {
                                "source_symbol": "SEAL",
                                "instrument_id": str(instrument_id),
                            }
                        ],
                    },
                ],
            }
            duplicate_canonical = json.dumps(
                duplicate_manifest, sort_keys=True, separators=(",", ":")
            )
            connection.execute(
                text(
                    "INSERT INTO market_data_coverage_manifests("
                    "dataset_version_id, universe_version_id, manifest_hash, manifest"
                    ") VALUES ("
                    ":version_id, :universe_version_id, :manifest_hash, CAST(:manifest AS JSONB)"
                    ")"
                ),
                {
                    "version_id": duplicate_manifest_version_id,
                    "universe_version_id": universe_version_id,
                    "manifest_hash": hashlib.sha256(
                        duplicate_canonical.encode("utf-8")
                    ).hexdigest(),
                    "manifest": duplicate_canonical,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO market_bars("
                    "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                    "open, high, low, close, volume) VALUES ("
                    ":version_id, :instrument_id, :t0, :t0, :t0, 100, 101, 99, 100, 1000)"
                ),
                {"version_id": good_version_id, "instrument_id": instrument_id, "t0": t0},
            )
            connection.execute(
                text(
                    "INSERT INTO market_bars("
                    "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                    "open, high, low, close, volume) VALUES ("
                    ":version_id, :instrument_id, :t0, :t0, :t0, 100, 101, 99, 100, 1000)"
                ),
                {
                    "version_id": membership_bypass_version_id,
                    "instrument_id": outsider_instrument_id,
                    "t0": t0,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO market_bars("
                    "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                    "open, high, low, close, volume) VALUES ("
                    ":version_id, :instrument_id, :t0, :t0, :t0, 100, 101, 99, 100, 1000)"
                ),
                {
                    "version_id": noncanonical_identity_version_id,
                    "instrument_id": instrument_id,
                    "t0": t0,
                },
            )
            for event_time, bound_instrument_id in (
                (t0, instrument_id),
                (t0 + timedelta(minutes=1), alternate_instrument_id),
            ):
                connection.execute(
                    text(
                        "INSERT INTO market_bars("
                        "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                        "open, high, low, close, volume) VALUES ("
                        ":version_id, :instrument_id, :event_time, :event_time, :event_time, "
                        "100, 101, 99, 100, 1000)"
                    ),
                    {
                        "version_id": identity_conflict_version_id,
                        "instrument_id": bound_instrument_id,
                        "event_time": event_time,
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO market_bars("
                    "dataset_version_id, instrument_id, event_time, available_time, ingestion_time, "
                    "open, high, low, close, volume) VALUES ("
                    ":version_id, :instrument_id, :t0, :t0, :t0, 100, 101, 99, 100, 1000)"
                ),
                {
                    "version_id": duplicate_manifest_version_id,
                    "instrument_id": instrument_id,
                    "t0": t0,
                },
            )

            sealer = SqlAlchemyMarketDataVersionSealer(connection)

            with pytest.raises(ValueError, match="MARKET_DATA_DATASET_VERSION_EMPTY"):
                sealer.seal(empty_version_id)
            with pytest.raises(ValueError, match="MARKET_DATA_DATASET_NOT_PIT_CERTIFIED"):
                sealer.seal(non_pit_version_id)
            with pytest.raises(ValueError, match="MARKET_DATA_DATASET_VERSION_EMPTY"):
                sealer.seal(fake_sealed_empty_version_id)
            with pytest.raises(ValueError, match="MARKET_DATA_DATASET_NOT_PIT_CERTIFIED"):
                sealer.seal(fake_sealed_non_pit_version_id)

            with pytest.raises(ValueError, match="MANIFEST_BACKED_FINALIZER_REQUIRED"):
                sealer.seal(good_version_id)
            with pytest.raises(ValueError, match="MANIFEST_BACKED_FINALIZER_REQUIRED"):
                sealer.seal(good_version_id)

            # Direct SQL cannot manufacture a sealed market-data version around
            # the manifest-backed finalizer once market bars exist.
            with pytest.raises(IntegrityError, match="FINALIZATION_REQUIRES_COVERAGE_MANIFEST"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE dataset_versions SET immutable = TRUE, vintage_label = 'sealed' "
                            "WHERE dataset_version_id = :version_id"
                        ),
                        {"version_id": good_version_id},
                    )

            with pytest.raises(
                IntegrityError,
                match="FINALIZATION_UNIVERSE_MEMBERSHIP_MISMATCH",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE dataset_versions "
                            "SET immutable = TRUE, vintage_label = 'sealed' "
                            "WHERE dataset_version_id = :version_id"
                        ),
                        {"version_id": membership_bypass_version_id},
                    )

            with pytest.raises(
                IntegrityError,
                match="FINALIZATION_DUPLICATE_MANIFEST_COVERAGE",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE dataset_versions "
                            "SET immutable = TRUE, vintage_label = 'sealed' "
                            "WHERE dataset_version_id = :version_id"
                        ),
                        {"version_id": duplicate_manifest_version_id},
                    )

            with pytest.raises(
                IntegrityError,
                match="FINALIZATION_IDENTITY_BINDING_CONFLICT",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE dataset_versions "
                            "SET immutable = TRUE, vintage_label = 'sealed' "
                            "WHERE dataset_version_id = :version_id"
                        ),
                        {"version_id": identity_conflict_version_id},
                    )

            with pytest.raises(
                IntegrityError,
                match="FINALIZATION_IDENTITY_NOT_CANONICAL",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE dataset_versions "
                            "SET immutable = TRUE, vintage_label = 'sealed' "
                            "WHERE dataset_version_id = :version_id"
                        ),
                        {"version_id": noncanonical_identity_version_id},
                    )

            # A row inserted already immutable is not sufficient evidence for
            # PIT consumption: the context boundary requires a durable manifest.
            context_repository = PITMarketContextRepository(connection)
            with pytest.raises(ValueError, match="MANIFEST_BACKED_SEALED_VERSION"):
                context_repository.get(
                    fake_sealed_empty_version_id,
                    as_of=t0,
                    instrument_ids=(),
                )

            sealed = connection.execute(
                text(
                    "SELECT immutable, vintage_label FROM dataset_versions "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": good_version_id},
            ).mappings().one()
            assert sealed["immutable"] is False
            assert sealed["vintage_label"] == "staging"
        finally:
            transaction.rollback()
