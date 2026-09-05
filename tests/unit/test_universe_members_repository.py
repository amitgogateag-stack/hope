from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, DateTime, MetaData, Table, Uuid, create_engine
from hope.domain.universe.models import UniverseMember
from hope.infrastructure.repositories.universe_members import UniverseMemberRepository


def test_universe_members_round_trip():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as connection:
        m = MetaData()
        Table('universe_members', m, Column('universe_version_id', Uuid, primary_key=True), Column('instrument_id', Uuid, primary_key=True), Column('valid_from', DateTime(timezone=True)), Column('valid_to', DateTime(timezone=True)))
        m.create_all(connection)
        repo = UniverseMemberRepository(connection)
        version_id = uuid4()
        member = UniverseMember(instrument_id=uuid4(), valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc), valid_to=datetime(2026, 2, 1, tzinfo=timezone.utc))
        repo.add(version_id, member)
        assert repo.list(version_id) == (member,)
