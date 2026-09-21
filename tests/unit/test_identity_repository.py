from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Column, DateTime, MetaData, String, Table, Uuid, create_engine
from hope.domain.identity.models import IdentityMapping, IdentityStatus, Instrument
from hope.infrastructure.repositories.identity import IdentityRepository


def schema(connection):
    m = MetaData()
    Table('instruments', m, Column('instrument_id', Uuid, primary_key=True), Column('canonical_symbol', String, nullable=False), Column('exchange', String, nullable=False), Column('status', String, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False))
    Table('broker_instruments', m, Column('broker_instrument_id', String, primary_key=True), Column('broker', String, nullable=False), Column('instrument_id', Uuid), Column('status', String, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False))
    Table('identity_mappings', m, Column('identity_mapping_id', Uuid, primary_key=True), Column('source_symbol', String, nullable=False), Column('broker_instrument_id', String, nullable=False), Column('canonical_instrument_id', Uuid), Column('status', String, nullable=False), Column('reason', String), Column('created_at', DateTime(timezone=True), nullable=False))
    m.create_all(connection)


def test_identity_round_trip_preserves_terminal_mapping():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as connection:
        schema(connection)
        repo = IdentityRepository(connection)
        now = datetime.now(timezone.utc)
        instrument_id = uuid4()
        instrument = Instrument(instrument_id=instrument_id, canonical_symbol='KPIL', exchange='NSE')
        repo.add_instrument(instrument, now)
        mapping = IdentityMapping(source_symbol='KALPATPOWR', broker_instrument_id='123', status=IdentityStatus.TERMINAL, reason='DATA_UNAVAILABLE_DUPLICATE_OF_CURRENT_MEMBER')
        repo.add_mapping(mapping, uuid4(), now)
        assert repo.get_instrument(instrument_id) == instrument
        assert repo.get_mapping('KALPATPOWR', '123') == mapping


def test_identity_lookup_is_bound_to_broker_namespace():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as connection:
        schema(connection)
        repo = IdentityRepository(connection)
        now = datetime.now(timezone.utc)
        first = IdentityMapping(
            source_symbol='SHARED',
            broker_instrument_id='broker-a-123',
            status=IdentityStatus.TERMINAL,
            reason='FIRST_BROKER_MAPPING',
        )
        second = IdentityMapping(
            source_symbol='SHARED',
            broker_instrument_id='broker-b-456',
            status=IdentityStatus.TERMINAL,
            reason='SECOND_BROKER_MAPPING',
        )
        repo.add_mapping(first, uuid4(), now)
        repo.add_mapping(second, uuid4(), now)

        assert repo.get_mapping('SHARED', 'broker-a-123') == first
        assert repo.get_mapping('SHARED', 'broker-b-456') == second


@pytest.mark.parametrize(
    ('source_symbol', 'broker_instrument_id'),
    [
        ('', 'broker-a-123'),
        (' SHARED', 'broker-a-123'),
        ('SHARED ', 'broker-a-123'),
        ('SHARED', ' broker-a-123'),
    ],
)
def test_identity_lookup_rejects_noncanonical_namespace(
    source_symbol: str,
    broker_instrument_id: str,
):
    engine = create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as connection:
        schema(connection)
        repo = IdentityRepository(connection)
        with pytest.raises(ValueError, match='IDENTITY_MAPPING_VALUE'):
            repo.get_mapping(source_symbol, broker_instrument_id)


@pytest.mark.parametrize("reason", [None, "", "   ", " padded reason "])
def test_identity_repository_rejects_noncanonical_stored_nonactive_reason(reason):
    engine = create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as connection:
        schema(connection)
        repo = IdentityRepository(connection)
        now = datetime.now(timezone.utc)
        connection.execute(
            repo._mappings.insert().values(
                identity_mapping_id=uuid4(),
                source_symbol='STALE',
                broker_instrument_id='broker-stale-1',
                canonical_instrument_id=None,
                status='TERMINAL',
                reason=reason,
                created_at=now,
            )
        )
        with pytest.raises(ValueError, match='IDENTITY_MAPPING_REASON|NON_ACTIVE identity mapping requires reason'):
            repo.get_mapping('STALE', 'broker-stale-1')
