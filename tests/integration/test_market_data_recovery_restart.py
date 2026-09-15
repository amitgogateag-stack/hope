import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from hope.infrastructure.market_data.ingestion import RawMarketBar
from hope.infrastructure.market_data.provider import MarketDataRequest, ProviderMarketDataBatch
from hope.infrastructure.market_data.recovery import ingest_market_data_recovery_windows
from hope.infrastructure.postgres.migrations import apply_migrations
from hope.infrastructure.repositories.market_bars import SqlAlchemyMarketBarSink
from hope.infrastructure.repositories.market_data_manifest import SqlAlchemyMarketDataCoverageManifestRepository
from hope.infrastructure.repositories.market_data_recovery import SqlAlchemyMarketDataRecoveryAuthorizer


class _Provider:
    source = "TEST"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def fetch_bars(self, request):
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _request(start):
    return MarketDataRequest(
        source="TEST",
        source_symbols=("ABC",),
        start=start,
        end=start + timedelta(minutes=1),
        interval=timedelta(minutes=1),
    )


def _batch(request):
    raw = RawMarketBar(
        source="TEST",
        source_symbol="ABC",
        event_time=request.start,
        available_time=request.start,
        ingestion_time=request.end,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
    )
    return ProviderMarketDataBatch(
        source="TEST", request=request, bars=(raw,), fetched_at=request.end
    )


@pytest.mark.integration
def test_market_data_recovery_is_durable_restart_safe_and_never_seals() -> None:
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
            dataset_id = uuid4()
            dataset_version_id = uuid4()
            universe_id = uuid4()
            universe_version_id = uuid4()
            t0 = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)
            first = _request(t0)
            second = _request(t0 + timedelta(minutes=1))
            outside = _request(t0 + timedelta(minutes=2))
            identity_map = {("TEST", "ABC"): instrument_id}

            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:id, :symbol, 'TEST', 'ACTIVE')"
                ),
                {"id": instrument_id, "symbol": f"RECOVERY-{instrument_id}"},
            )
            connection.execute(
                text("INSERT INTO universes(universe_id, name) VALUES (:id, :name)"),
                {"id": universe_id, "name": f"recovery-{universe_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_versions("
                    "universe_version_id, universe_id, version, pit_certified, declared_member_count"
                    ") VALUES (:version_id, :universe_id, 'v1', TRUE, 1)"
                ),
                {"version_id": universe_version_id, "universe_id": universe_id},
            )
            connection.execute(
                text(
                    "INSERT INTO universe_members(universe_version_id, instrument_id) "
                    "VALUES (:version_id, :instrument_id)"
                ),
                {"version_id": universe_version_id, "instrument_id": instrument_id},
            )
            connection.execute(
                text(
                    "INSERT INTO datasets(dataset_id, name, source, pit_certified) "
                    "VALUES (:id, :name, 'TEST', TRUE)"
                ),
                {"id": dataset_id, "name": f"recovery-{dataset_id}"},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions("
                    "dataset_version_id, dataset_id, version, vintage_label, immutable"
                    ") VALUES (:version_id, :dataset_id, 'v1', 'staging', FALSE)"
                ),
                {"version_id": dataset_version_id, "dataset_id": dataset_id},
            )

            SqlAlchemyMarketDataCoverageManifestRepository(connection).declare(
                dataset_version_id,
                universe_version_id,
                (first, second),
                identity_map=identity_map,
            )

            # First process incarnation persists only the first authorized subset.
            first_provider = _Provider((_batch(first),))
            ingest_market_data_recovery_windows(
                first_provider,
                SqlAlchemyMarketBarSink(connection),
                SqlAlchemyMarketDataRecoveryAuthorizer(connection),
                dataset_version_id,
                (first,),
                identity_map=identity_map,
            )
            assert connection.execute(
                text("SELECT count(*) FROM market_bars WHERE dataset_version_id=:id"),
                {"id": dataset_version_id},
            ).scalar_one() == 1

            state = connection.execute(
                text(
                    "SELECT immutable, vintage_label FROM dataset_versions "
                    "WHERE dataset_version_id=:id"
                ),
                {"id": dataset_version_id},
            ).mappings().one()
            assert state["immutable"] is False
            assert state["vintage_label"] == "staging"

            # Simulated restart: new authorizer/sink, exact retry is a durable DB no-op,
            # then the remaining declared subset is recovered.
            restart_provider = _Provider((_batch(first), _batch(second)))
            restart_authorizer = SqlAlchemyMarketDataRecoveryAuthorizer(connection)
            restart_sink = SqlAlchemyMarketBarSink(connection)
            ingest_market_data_recovery_windows(
                restart_provider, restart_sink, restart_authorizer,
                dataset_version_id, (first,), identity_map=identity_map,
            )
            assert connection.execute(
                text("SELECT count(*) FROM market_bars WHERE dataset_version_id=:id"),
                {"id": dataset_version_id},
            ).scalar_one() == 1

            ingest_market_data_recovery_windows(
                restart_provider, restart_sink, restart_authorizer,
                dataset_version_id, (second,), identity_map=identity_map,
            )
            assert connection.execute(
                text("SELECT count(*) FROM market_bars WHERE dataset_version_id=:id"),
                {"id": dataset_version_id},
            ).scalar_one() == 2

            state = connection.execute(
                text(
                    "SELECT immutable, vintage_label FROM dataset_versions "
                    "WHERE dataset_version_id=:id"
                ),
                {"id": dataset_version_id},
            ).mappings().one()
            assert state["immutable"] is False
            assert state["vintage_label"] == "staging"

            # An out-of-manifest recovery request fails before provider access.
            rejected_provider = _Provider((_batch(outside),))
            with pytest.raises(ValueError, match="RECOVERY_REQUEST"):
                ingest_market_data_recovery_windows(
                    rejected_provider,
                    restart_sink,
                    restart_authorizer,
                    dataset_version_id,
                    (outside,),
                    identity_map=identity_map,
                )
            assert rejected_provider.calls == []

            # Identity substitution also fails before provider access.
            wrong_identity_provider = _Provider((_batch(first),))
            with pytest.raises(ValueError, match="IDENTITY_BINDING_MISMATCH"):
                ingest_market_data_recovery_windows(
                    wrong_identity_provider,
                    restart_sink,
                    restart_authorizer,
                    dataset_version_id,
                    (first,),
                    identity_map={("TEST", "ABC"): uuid4()},
                )
            assert wrong_identity_provider.calls == []
        finally:
            transaction.rollback()
