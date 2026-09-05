from datetime import datetime, timezone
from uuid import uuid4
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
        assert repo.get_mapping('KALPATPOWR') == mapping
