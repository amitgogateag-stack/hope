import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from hope.infrastructure.postgres.migrations import apply_migrations


@pytest.mark.integration
def test_identity_mapping_contract_is_enforced_by_database() -> None:
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    migrations_dir = Path(__file__).parents[2] / "migrations"

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            apply_migrations(connection, migrations_dir)
            canonical_instrument_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO instruments(instrument_id, canonical_symbol, exchange, status) "
                    "VALUES (:instrument_id, 'IDENTITY-CONTRACT', 'TEST', 'ACTIVE')"
                ),
                {"instrument_id": canonical_instrument_id},
            )
            for broker, broker_instrument_id in (
                ("TEST-VALID", "broker-valid-1"),
                ("TEST-PADDED", " broker-padded-1 "),
            ):
                connection.execute(
                    text(
                        "INSERT INTO broker_instruments"
                        "(broker_instrument_id, broker, instrument_id, status) "
                        "VALUES (:broker_instrument_id, :broker, :instrument_id, 'ACTIVE')"
                    ),
                    {
                        "broker": broker,
                        "broker_instrument_id": broker_instrument_id,
                        "instrument_id": canonical_instrument_id,
                    },
                )

            invalid_mappings = (
                ("", "broker-valid-1", None, "TERMINAL", "RETIRED"),
                ("PADDED-BROKER", " broker-padded-1 ", None, "TERMINAL", "RETIRED"),
                ("ACTIVE-UNBOUND", "broker-valid-1", None, "ACTIVE", None),
                (
                    "TERMINAL-BOUND",
                    "broker-valid-1",
                    canonical_instrument_id,
                    "TERMINAL",
                    "RETIRED",
                ),
                ("NONACTIVE-NO-REASON", "broker-valid-1", None, "UNRESOLVED", None),
                ("PADDED-REASON", "broker-valid-1", None, "TERMINAL", " RETIRED "),
            )
            statement = text(
                "INSERT INTO identity_mappings"
                "(identity_mapping_id, source_symbol, broker_instrument_id, "
                "canonical_instrument_id, status, reason) "
                "VALUES (:identity_mapping_id, :source_symbol, :broker_instrument_id, "
                ":canonical_instrument_id, :status, :reason)"
            )
            for source_symbol, broker_id, instrument_id, status, reason in invalid_mappings:
                with pytest.raises(IntegrityError):
                    with connection.begin_nested():
                        connection.execute(
                            statement,
                            {
                                "identity_mapping_id": uuid4(),
                                "source_symbol": source_symbol,
                                "broker_instrument_id": broker_id,
                                "canonical_instrument_id": instrument_id,
                                "status": status,
                                "reason": reason,
                            },
                        )

            connection.execute(
                statement,
                {
                    "identity_mapping_id": uuid4(),
                    "source_symbol": "ACTIVE-BOUND",
                    "broker_instrument_id": "broker-valid-1",
                    "canonical_instrument_id": canonical_instrument_id,
                    "status": "ACTIVE",
                    "reason": None,
                },
            )
            connection.execute(
                statement,
                {
                    "identity_mapping_id": uuid4(),
                    "source_symbol": "TERMINAL-REASONED",
                    "broker_instrument_id": "broker-valid-1",
                    "canonical_instrument_id": None,
                    "status": "TERMINAL",
                    "reason": "RETIRED",
                },
            )
        finally:
            transaction.rollback()
