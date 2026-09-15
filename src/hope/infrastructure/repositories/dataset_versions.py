from __future__ import annotations

from uuid import UUID

from sqlalchemy import Connection, text


class SqlAlchemyMarketDataVersionSealer:
    """Legacy fail-closed boundary for market-data version sealing.

    New sealing transitions require SqlAlchemyMarketDataVersionFinalizer, which
    proves exact persisted coverage against an immutable manifest. This class
    retains validation and safe no-op behavior only for already sealed versions.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def seal(self, dataset_version_id: UUID) -> None:
        if not isinstance(dataset_version_id, UUID):
            raise TypeError("MARKET_DATA_SEAL_REQUIRES_DATASET_VERSION_ID")

        with self._connection.begin_nested():
            row = self._connection.execute(
                text(
                    "SELECT dv.immutable, dv.vintage_label, d.pit_certified "
                    "FROM dataset_versions dv "
                    "JOIN datasets d ON d.dataset_id = dv.dataset_id "
                    "WHERE dv.dataset_version_id = :version_id "
                    "FOR UPDATE"
                ),
                {"version_id": dataset_version_id},
            ).mappings().one_or_none()

            if row is None:
                raise ValueError("MARKET_DATA_DATASET_VERSION_NOT_FOUND")
            if row["immutable"] and row["vintage_label"] != "sealed":
                raise ValueError("MARKET_DATA_DATASET_VERSION_ALREADY_IMMUTABLE")
            if not row["pit_certified"]:
                raise ValueError("MARKET_DATA_DATASET_NOT_PIT_CERTIFIED")

            bar_count = self._connection.execute(
                text(
                    "SELECT count(*) FROM market_bars "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            ).scalar_one()
            if bar_count <= 0:
                raise ValueError("MARKET_DATA_DATASET_VERSION_EMPTY")

            if row["immutable"]:
                return

            raise ValueError("MARKET_DATA_MANIFEST_BACKED_FINALIZER_REQUIRED")
