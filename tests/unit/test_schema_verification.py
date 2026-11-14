from types import SimpleNamespace
import json
import pytest
from hope.infrastructure.postgres.schema_verification import schema_observation


@pytest.mark.parametrize('fault',[None,'write','isolation','version','statement','lock','empty','overflow','bytes','null_definition','secret'])
def test_catalog_is_bounded_readonly_and_never_self_approves(fault):
    settings=dict(server_version_num='170000',transaction_read_only='on',transaction_isolation='repeatable read',statement_timeout='5s',lock_timeout='1s')
    rows=[('function','approved','private body never exposed')];queries=[]
    if fault=='write':settings['transaction_read_only']='off'
    elif fault=='isolation':settings['transaction_isolation']='read committed'
    elif fault=='version':settings['server_version_num']='150000'
    elif fault=='statement':settings['statement_timeout']='0'
    elif fault=='lock':settings['lock_timeout']='1min'
    elif fault=='empty':rows=[]
    elif fault=='overflow':rows=rows*10001
    elif fault=='null_definition':rows=[('function','approved',None)]
    elif fault=='bytes':rows=[('function','approved','x'*(4*1024*1024))]
    def execute(sql):
        sql=str(sql);queries.append(sql)
        if fault=='secret':raise RuntimeError('private database connection secret')
        return SimpleNamespace(scalar_one=lambda:settings[sql[5:]]) if sql.startswith('SHOW ') else rows
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),execute=execute)
    observed=schema_observation(connection)
    assert observed['state']==('UNVERIFIED' if fault is None else 'BLOCKED')
    assert 'private' not in json.dumps(observed)
    assert all(q.startswith(('SHOW ','\nSELECT')) for q in queries)
    if fault is None:
        assert schema_observation(connection,observed['sha256'])['state']=='VERIFIED'
        assert schema_observation(connection,'0'*64)['state']=='MISMATCH'
        assert schema_observation(connection,'invalid secret')['state']=='BLOCKED'


def test_catalog_rejects_forged_string_digest_equality():
    class ForgedDigest(str):
        def __eq__(self,other):return True

    settings=dict(server_version_num='170000',transaction_read_only='on',transaction_isolation='repeatable read',statement_timeout='5s',lock_timeout='1s')
    rows=[('function','approved','definition')]
    def execute(sql):
        sql=str(sql)
        return SimpleNamespace(scalar_one=lambda:settings[sql[5:]]) if sql.startswith('SHOW ') else rows
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),execute=execute)
    forged=ForgedDigest('0'*64)
    assert forged==schema_observation(connection)['sha256']
    assert schema_observation(connection,forged)==dict(schema='hope.schema-catalog.v1',state='BLOCKED',
        code='SCHEMA_CATALOG_UNVERIFIED',sha256=None,objects=None,database_writes=False,execution_authorized=False)


@pytest.mark.parametrize('code',['PAPER_SINGLE_CYCLE_GUARD_REQUIRED','PAPER_SINGLE_CYCLE_PLAN_MISMATCH'])
def test_single_cycle_blocker_is_preserved_in_bounded_dashboard_diagnostics(code):
    from hope.infrastructure.paper_service import _failure_state
    from hope.api.dashboard_server import OPERATIONS_ERROR_CODES
    assert _failure_state(RuntimeError(code))==('UNAVAILABLE',code)
    assert code in OPERATIONS_ERROR_CODES



def test_catalog_digest_is_order_independent_but_preserves_definition_and_multiplicity():
    settings=dict(server_version_num='170000',transaction_read_only='on',transaction_isolation='repeatable read',statement_timeout='5s',lock_timeout='1s')
    rows=[('constraint','same.identity','definition A'),('constraint','same.identity','definition B'),('column','other.identity','definition C')]
    def execute(sql):
        sql=str(sql)
        return SimpleNamespace(scalar_one=lambda:settings[sql[5:]]) if sql.startswith('SHOW ') else list(rows)
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),execute=execute)
    original=schema_observation(connection)['sha256']
    rows.reverse()
    assert schema_observation(connection,original)['state']=='VERIFIED'
    rows.append(rows[0])
    assert schema_observation(connection,original)['state']=='MISMATCH'
    rows.pop();rows[0]=(rows[0][0],rows[0][1],'changed definition')
    assert schema_observation(connection,original)['state']=='MISMATCH'
