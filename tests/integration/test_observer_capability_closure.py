"""Real PostgreSQL privilege drift; no production grants or admission proof."""
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_startup_transition_postgres import (
    isolated_database, startup_database, seeded, prepare, certified_case,
)
from test_financial_lifecycle_observer import observer, completed
from hope.security.financial_lifecycle import LifecycleDenied, inspect_committed_cycle

pytestmark = pytest.mark.integration


@pytest.fixture
def capability_probe(startup_database, observer):
    admin = startup_database['admin']
    suffix = uuid4().hex
    table, writer, bridge = 'hope_probe_'+suffix, 'hope_writer_'+suffix, 'hope_bridge_'+suffix
    with admin.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE public.'+table+' (id int PRIMARY KEY,value int NOT NULL)')
        connection.exec_driver_sql('INSERT INTO public.'+table+' VALUES (1,0)')
        connection.exec_driver_sql('GRANT SELECT ON public.'+table+' TO '+observer.url.username)
        for role in (writer, bridge):
            connection.exec_driver_sql('CREATE ROLE '+role+
                ' NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
            connection.exec_driver_sql('GRANT USAGE ON SCHEMA public TO '+role)
            connection.exec_driver_sql('GRANT SELECT ON public.'+table+' TO '+role)
    yield dict(admin=admin, table=table, writer=writer, bridge=bridge)
    observer.dispose()
    with admin.begin() as connection:
        for role in (bridge, writer):
            connection.exec_driver_sql('DROP OWNED BY '+role)
            connection.exec_driver_sql('DROP ROLE '+role)
        connection.exec_driver_sql('DROP TABLE public.'+table)


def _deny(observer, ref):
    with pytest.raises(LifecycleDenied, match='OBSERVER_CAPABILITY_BOUNDARY_UNSAFE'):
        inspect_committed_cycle(observer, ref)


def _membership(probe, observer, depth):
    roles = [probe['writer']]
    if depth == 2:
        roles.append(probe['bridge'])
    roles.append(observer.url.username)
    with probe['admin'].begin() as connection:
        for parent, child in zip(roles, roles[1:]):
            connection.exec_driver_sql('GRANT '+parent+' TO '+child+' WITH INHERIT FALSE, SET TRUE')
    with observer.connect() as connection:
        row = connection.execute(text("SELECT pg_has_role(current_user,:role,'USAGE'), "
            "pg_has_role(current_user,:role,'SET')"), {'role': probe['writer']}).one()
        assert tuple(row) == (False, True)


def test_column_cash_right_cannot_pass_as_read_only(prepare, observer, startup_database):
    _, ref = completed(prepare)
    with startup_database['admin'].begin() as connection:
        connection.exec_driver_sql('GRANT UPDATE(cash) ON public.paper_portfolios TO '+observer.url.username)
    with observer.connect() as connection:
        row = connection.execute(text("SELECT has_table_privilege(current_user,'public.paper_portfolios','UPDATE'), "
            "has_any_column_privilege(current_user,'public.paper_portfolios','UPDATE')")).one()
        assert tuple(row) == (False, True)  # table-only inspection misses this grant
        before = connection.execute(text('SELECT cash FROM public.paper_portfolios WHERE portfolio_id=:id'),
            {'id': ref.portfolio_id}).scalar_one()
    _deny(observer, ref)
    # Keep the independent migration237 financial protection intact. The SQL
    # reaches that trigger (23514), not a permission failure (42501).
    with pytest.raises(DBAPIError, match='PAPER_ADMISSION_PRINCIPAL_UNCLASSIFIED') as caught:
        with observer.begin() as connection:
            connection.execute(text('UPDATE public.paper_portfolios SET cash=cash+1 WHERE portfolio_id=:id'),
                {'id': ref.portfolio_id})
    assert caught.value.orig.sqlstate == '23514'
    with observer.connect() as connection:
        assert connection.execute(text('SELECT cash FROM public.paper_portfolios WHERE portfolio_id=:id'),
            {'id': ref.portfolio_id}).scalar_one() == before


def test_column_mutation_of_unlisted_public_relation_is_rejected(prepare, observer, capability_probe):
    _, ref = completed(prepare)
    p = capability_probe
    with p['admin'].begin() as connection:
        connection.exec_driver_sql('GRANT UPDATE(value) ON public.'+p['table']+' TO '+observer.url.username)
    _deny(observer, ref)
    with observer.connect() as connection:
        transaction = connection.begin()
        assert connection.exec_driver_sql('UPDATE public.'+p['table']+' SET value=1 WHERE id=1 RETURNING value').scalar_one() == 1
        transaction.rollback()
    with observer.connect() as connection:
        assert connection.exec_driver_sql('SELECT value FROM public.'+p['table']).scalar_one() == 0


@pytest.mark.parametrize('depth', [1, 2])
def test_noninherited_set_role_chain_cannot_hide_writer(prepare, observer, capability_probe, depth):
    _, ref = completed(prepare)
    p = capability_probe
    with p['admin'].begin() as connection:
        connection.exec_driver_sql('GRANT UPDATE(value) ON public.'+p['table']+' TO '+p['writer'])
    _membership(p, observer, depth)
    with observer.connect() as connection:
        assert connection.execute(text("SELECT has_any_column_privilege(current_user,:table,'UPDATE')"),
            {'table': 'public.'+p['table']}).scalar_one() is False
    _deny(observer, ref)
    with observer.connect() as connection:
        transaction = connection.begin()
        connection.exec_driver_sql('SET LOCAL ROLE '+p['writer'])
        assert connection.exec_driver_sql('UPDATE public.'+p['table']+' SET value=1 RETURNING value').scalar_one() == 1
        transaction.rollback()
    with observer.connect() as connection:
        assert connection.exec_driver_sql('SELECT value FROM public.'+p['table']).scalar_one() == 0


def test_noninherited_sequence_right_cannot_hide_persistent_mutation(prepare, observer, capability_probe):
    _, ref = completed(prepare)
    p = capability_probe
    sequence = 'public.paper_environment_control_events_control_sequence_seq'
    with p['admin'].begin() as connection:
        before = tuple(connection.exec_driver_sql('SELECT last_value,is_called FROM '+sequence).one())
        connection.exec_driver_sql('GRANT USAGE ON SEQUENCE '+sequence+' TO '+p['writer'])
    _membership(p, observer, 2)
    with observer.connect() as connection:
        assert connection.execute(text("SELECT has_sequence_privilege(current_user,:seq,'USAGE,UPDATE')"),
            {'seq': sequence}).scalar_one() is False
    _deny(observer, ref)
    try:
        with observer.begin() as connection:
            connection.exec_driver_sql('SET LOCAL ROLE '+p['writer'])
            advanced = connection.execute(text('SELECT nextval(CAST(:seq AS regclass))'), {'seq': sequence}).scalar_one()
        with p['admin'].connect() as connection:
            assert tuple(connection.exec_driver_sql('SELECT last_value,is_called FROM '+sequence).one()) == (advanced, True)
        assert advanced == before[0]+1
    finally:
        with p['admin'].begin() as connection:
            connection.execute(text('SELECT setval(CAST(:seq AS regclass),:value,:called)'),
                {'seq': sequence, 'value': before[0], 'called': before[1]})
    with p['admin'].connect() as connection:
        assert tuple(connection.exec_driver_sql('SELECT last_value,is_called FROM '+sequence).one()) == before


def test_noninherited_control_writer_is_rejected_before_halt_append(prepare, observer, capability_probe):
    _, ref = completed(prepare)
    p = capability_probe
    sequence = 'public.paper_environment_control_events_control_sequence_seq'
    with p['admin'].begin() as connection:
        before = tuple(connection.exec_driver_sql('SELECT last_value,is_called FROM '+sequence).one())
        count = connection.exec_driver_sql('SELECT count(*) FROM public.paper_environment_control_events').scalar_one()
        connection.exec_driver_sql('GRANT SELECT,INSERT ON public.paper_environment_control_events TO '+p['writer'])
        connection.exec_driver_sql('GRANT USAGE,SELECT ON SEQUENCE '+sequence+' TO '+p['writer'])
    _membership(p, observer, 2)
    _deny(observer, ref)
    try:
        with observer.connect() as connection:
            transaction = connection.begin()
            connection.exec_driver_sql('SET LOCAL ROLE '+p['writer'])
            connection.execute(text("INSERT INTO public.paper_environment_control_events(state,reason) "
                "VALUES ('HALTED','DISPOSABLE_DELEGATED_OBSERVER_PROBE')"))
            assert connection.exec_driver_sql('SELECT count(*) FROM public.paper_environment_control_events').scalar_one() == count+1
            transaction.rollback()
    finally:
        with p['admin'].begin() as connection:
            connection.execute(text('SELECT setval(CAST(:seq AS regclass),:value,:called)'),
                {'seq': sequence, 'value': before[0], 'called': before[1]})
    with p['admin'].connect() as connection:
        assert connection.exec_driver_sql('SELECT count(*) FROM public.paper_environment_control_events').scalar_one() == count
        assert tuple(connection.exec_driver_sql('SELECT last_value,is_called FROM '+sequence).one()) == before


def test_unlisted_approval_write_grant_cannot_pass_inventory(prepare, observer, startup_database):
    _, ref = completed(prepare)
    with startup_database['admin'].begin() as connection:
        connection.exec_driver_sql('GRANT INSERT ON public.paper_admission_approvals TO '+observer.url.username)
    _deny(observer, ref)
    with pytest.raises(DBAPIError, match='PAPER_ADMISSION_TRUSTED_WRITER_REQUIRED') as caught:
        with observer.begin() as connection:
            connection.execute(text("INSERT INTO public.paper_admission_approvals "
                "(target_table,authorization_sha256,principal_oid,approved_row,valid_until) "
                "VALUES ('paper_startup_executions',repeat('0',64),1,'{}',1)"))
    assert caught.value.orig.sqlstate == '23514'


def test_trigger_capability_is_rejected_before_catalog_mutation(prepare, observer, capability_probe):
    _, ref = completed(prepare)
    p = capability_probe
    function = p['table']+'_trigger'
    with p['admin'].begin() as connection:
        connection.exec_driver_sql('CREATE FUNCTION public.'+function+
            "() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'")
        connection.exec_driver_sql('GRANT TRIGGER ON public.'+p['table']+' TO '+observer.url.username)
    try:
        _deny(observer, ref)
        with observer.connect() as connection:
            transaction = connection.begin()
            connection.exec_driver_sql('CREATE TRIGGER probe_trigger BEFORE UPDATE ON public.'+p['table']+
                ' FOR EACH ROW EXECUTE FUNCTION public.'+function+'()')
            assert connection.execute(text('SELECT count(*) FROM pg_catalog.pg_trigger '
                'WHERE tgrelid=CAST(:table AS regclass) AND tgname=\'probe_trigger\''),
                {'table': 'public.'+p['table']}).scalar_one() == 1
            transaction.rollback()
        with p['admin'].connect() as connection:
            assert connection.execute(text('SELECT count(*) FROM pg_catalog.pg_trigger '
                'WHERE tgrelid=CAST(:table AS regclass) AND tgname=\'probe_trigger\''),
                {'table': 'public.'+p['table']}).scalar_one() == 0
    finally:
        with p['admin'].begin() as connection:
            connection.exec_driver_sql('DROP FUNCTION public.'+function+'()')


def test_column_reference_capability_is_not_a_read_only_grant(prepare, observer, capability_probe):
    _, ref = completed(prepare)
    p = capability_probe
    with p['admin'].begin() as connection:
        connection.exec_driver_sql('GRANT REFERENCES(id) ON public.'+p['table']+' TO '+observer.url.username)
    with observer.connect() as connection:
        assert connection.execute(text('SELECT has_any_column_privilege(current_user,:table,\'REFERENCES\')'),
            {'table': 'public.'+p['table']}).scalar_one() is True
    _deny(observer, ref)


def test_version_specific_maintenance_privilege_is_handled(prepare, observer, capability_probe):
    _, ref = completed(prepare)
    p = capability_probe
    with p['admin'].connect() as connection:
        major = connection.execute(text("SELECT current_setting('server_version_num')::int/10000")).scalar_one()
    if major == 16:
        with pytest.raises(DBAPIError) as caught:
            with observer.connect() as connection:
                connection.execute(text("SELECT has_table_privilege(current_user,:table,'MAINTAIN')"),
                    {'table': 'public.'+p['table']})
        assert caught.value.orig.sqlstate == '22023'
        # PG16 has no MAINTAIN selector. The actual observer must still work;
        # it cannot evaluate the PG17-only inquiry on this server.
        assert inspect_committed_cycle(observer, ref)['execution_authorized'] is False
    else:
        assert major == 17
        with p['admin'].begin() as connection:
            connection.exec_driver_sql('GRANT MAINTAIN ON public.'+p['table']+' TO '+observer.url.username)
        _deny(observer, ref)
        with observer.begin() as connection:
            connection.exec_driver_sql('ANALYZE public.'+p['table'])
