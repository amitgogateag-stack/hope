from __future__ import annotations

from uuid import UUID

from sqlalchemy import Connection, text


class SqlAlchemyMarketDataVersionSealer:
    """Seal one persisted market-data dataset version exactly once.

    A version may be sealed only when it belongs to a PIT-certified dataset and
    already contains market bars. The row is locked during validation/sealing.
    Exact retries after a successful seal are no-ops only when the sealed
    version still satisfies the same evidence requirements; other immutable
    versions are rejected rather than silently reclassified as market-data
    evidence.
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

            self._connection.execute(
                text(
                    "UPDATE dataset_versions "
                    "SET immutable = TRUE, vintage_label = 'sealed' "
                    "WHERE dataset_version_id = :version_id"
                ),
                {"version_id": dataset_version_id},
            )
