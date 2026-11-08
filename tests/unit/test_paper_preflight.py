"""Synthetic read-only preflight contracts; native PG tests prove transaction behavior."""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest

from hope.api import paper_preflight as p
from test_paper_controller import SHA, RECEIPT, FACTORY, NOW, verified


@pytest.fixture
def records(tmp_path,monkeypatch):
    # Model filesystem trust only for pure reconciliation/normalization tests.
    # Real ownership and O_NOFOLLOW behavior remain native CI tests.
    monkeypatch.setattr(p.runtime,'_trusted',lambda path,**kw:p.runtime._identity(Path(path).stat()))
    monkeypatch.setattr(p.runtime,'_read',lambda path,limit,**kw:Path(path).read_bytes())
    directory=tmp_path/'migrations';directory.mkdir()
    (directory/'001_first.sql').write_bytes(b'SELECT 1;\r\n')
    (directory/'002_second.sql').write_bytes(b'SELECT 2;\n')
    expected,_=p.inventory(directory)
    assert expected['001_first.sql']==hashlib.sha256(b'SELECT 1;\n').hexdigest()
    rows=list(expected.items())
    settings=dict(transaction_read_only='on',server_version_num='170000',transaction_isolation='repeatable read',statement_timeout='5s',lock_timeout='1s')
    queries=[]
    def execute(sql,params=None):
        sql=str(sql);queries.append(sql)
        if sql.startswith('SHOW '):return SimpleNamespace(scalar_one=lambda:settings[sql[5:]])
        if 'to_regclass' in sql:return SimpleNamespace(scalar_one=lambda:'hope_schema_migrations')
        assert sql.startswith('SELECT version, checksum')
        return list(rows)
    connection=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),execute=execute)
    return directory,rows,settings,connection,queries


@pytest.mark.parametrize('fault',[None,'pending','unknown','mismatch','unsigned','duplicate','invalid_checksum',
                                 'readwrite','readcommitted','unbounded_statement','unbounded_lock','long_timeout','isolation','postgres_major'])
def test_readonly_migration_record_reconciliation(records,fault):
    directory,rows,settings,connection,queries=records
    if fault=='pending':rows.pop()
    elif fault=='unknown':rows.append(('private.sql','f'*64))
    elif fault=='mismatch':rows[0]=(rows[0][0],'f'*64)
    elif fault=='unsigned':rows[0]=(rows[0][0],None)
    elif fault=='duplicate':rows.append(rows[0])
    elif fault=='invalid_checksum':rows[0]=(rows[0][0],'private secret')
    elif fault=='readwrite':settings['transaction_read_only']='off'
    elif fault=='readcommitted':settings['transaction_isolation']='read committed'
    elif fault=='unbounded_statement':settings['statement_timeout']='0'
    elif fault=='unbounded_lock':settings['lock_timeout']='0'
    elif fault=='long_timeout':settings['statement_timeout']='1min'
    elif fault=='isolation':settings['transaction_isolation']='private secret'
    elif fault=='postgres_major':settings['server_version_num']='150000'
    before=deepcopy(rows)
    result=p.migration_compatibility(connection,directory)
    assert result['state']==('VERIFIED' if fault is None else 'UNVERIFIED' if fault=='unsigned' else 'BLOCKED')
    assert rows==before and all(query.startswith(('SHOW ','SELECT ')) for query in queries)
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('fault',['missing','extra','symlink','checksum','replaced','overflow','schema'])
def test_migration_storage_query_bounds_and_closing_changes(records,monkeypatch,fault):
    directory,rows,settings,connection,queries=records
    schema='public'
    if fault=='missing':
        execute=connection.execute
        connection.execute=lambda sql,params=None:SimpleNamespace(scalar_one=lambda:None) if 'to_regclass' in str(sql) else execute(sql,params)
    elif fault=='extra':(directory/'003_Private.sql').write_text('SELECT 3;')
    elif fault=='symlink':
        # Isolate a rejected trust check without requiring Windows link rights.
        monkeypatch.setattr(p.runtime,'_trusted',Mock(side_effect=RuntimeError('private link')))
    elif fault=='checksum':
        original=p.inventory
        calls=[]
        def changing(path):
            calls.append(True)
            if len(calls)==2:(directory/'001_first.sql').write_text('SELECT 7;')
            return original(path)
        monkeypatch.setattr(p,'inventory',changing)
    elif fault=='replaced':
        execute=connection.execute
        def changing(sql,params=None):
            if str(sql).startswith('SELECT version') and sum(q.startswith('SELECT version') for q in queries)==1:rows.pop()
            return execute(sql,params)
        connection.execute=changing
    elif fault=='overflow':rows[:]=[(str(index),'a'*64) for index in range(1001)]
    else:schema='public; DROP TABLE private'
    result=p.migration_compatibility(connection,directory,schema=schema)
    assert result['state']=='BLOCKED' and 'private' not in json.dumps(result)


def test_absent_connection_never_reads_files_or_creates_database_objects(tmp_path,monkeypatch):
    inventory=Mock();monkeypatch.setattr(p,'inventory',inventory)
    assert p.migration_compatibility(None,tmp_path)['state']=='UNVERIFIED'
    inventory.assert_not_called()


@pytest.fixture
def preflight(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'_activation_guard',lambda *a,**k:nullcontext())
    monkeypatch.setattr(p.runtime,'_trusted',Mock(return_value=('stable',)))
    monkeypatch.setattr(p.runtime,'source_snapshot',Mock(return_value={'stable':'source'}))
    monkeypatch.setattr(p,'inspect_release',Mock())
    host=dict(python_version=[3,13],systemd_major=249,postgres_major=17)
    monkeypatch.setattr(p.deployment,'host_observation',Mock(side_effect=lambda **kw:dict(host)))
    migration=dict(state='VERIFIED',code='MIGRATION_RECORDS_VERIFIED',expected=2,applied=2,pending=0,unknown=0,mismatched=0,unsigned=0)
    monkeypatch.setattr(p,'migration_compatibility',Mock(side_effect=lambda *a,**k:dict(migration)))
    image=dict(state='VERIFIED',scope='PRODUCTION_PAPER',expires_at=NOW+300)
    monkeypatch.setattr(p.deployment,'verify_image',Mock(side_effect=lambda *a,**k:dict(image)))
    proof=verified()
    monkeypatch.setattr(p.deployment,'inspect_deployment',Mock(side_effect=lambda *a,**k:deepcopy(proof)))
    observed=dict(state='VERIFIED',expires_at=NOW+60)
    monkeypatch.setattr(p.controller,'observation',Mock(side_effect=lambda *a,**k:dict(observed)))
    return tmp_path,host,migration,image,proof,observed


@pytest.mark.parametrize('fault',[None,'source','host','postgres','migration','image','lab','deployment','controller',
                                 'receipt','factory','closing_source','closing_deployment','closing_controller','expiry'])
def test_complete_preflight_requires_every_independent_stage_and_closure(preflight,monkeypatch,fault):
    root,host,migration,image,proof,observed=preflight
    receipt,factory=RECEIPT,FACTORY
    if fault=='source':p.inspect_release.side_effect=RuntimeError('private path')
    elif fault=='host':host['python_version']=[3,12]
    elif fault=='postgres':host['postgres_major']=None
    elif fault=='migration':migration.update(state='BLOCKED',code='REVIEWED_MIGRATIONS_PENDING')
    elif fault=='image':p.deployment.verify_image.side_effect=RuntimeError('private')
    elif fault=='lab':image['scope']='DISPOSABLE_LAB_ONLY'
    elif fault=='deployment':proof['state']='BLOCKED'
    elif fault=='controller':observed['state']='HALTED'
    elif fault=='receipt':receipt=None
    elif fault=='factory':factory=None
    elif fault=='closing_source':p.inspect_release.side_effect=[None,RuntimeError('changed private source')]
    elif fault=='closing_deployment':p.deployment.inspect_deployment.side_effect=[deepcopy(proof),dict(proof,state='BLOCKED')]
    elif fault=='closing_controller':p.controller.observation.side_effect=[dict(observed),dict(observed,state='HALTED')]
    elif fault=='expiry':observed['expires_at']=NOW
    result=p.inspect_preflight(root,SHA,receipt=receipt,factory=factory,now=NOW)
    assert result['state']==('VERIFIED' if fault is None else 'BLOCKED')
    assert all(result[field] is False for field in ('execution_authorized','host_changes','database_writes','live_enabled'))
    assert set(result['stages'])==set(p.STAGES)
    assert 'private' not in json.dumps(result) and str(root) not in json.dumps(result)
    if fault in {'closing_source','closing_deployment','closing_controller','expiry'}:
        assert all(stage['state']!='VERIFIED' for stage in result['stages'].values())


def test_cli_never_connects_without_opt_in_or_before_source_validation(tmp_path,monkeypatch,capsys):
    engine=Mock();monkeypatch.setattr(p,'create_engine',engine)
    assert p.main([SHA,'--root',str(tmp_path)])==1
    result=json.loads(capsys.readouterr().out)
    assert result['state']=='BLOCKED' and result['execution_authorized'] is False
    engine.assert_not_called()
    assert p.main(['invalid','--root',str(tmp_path),'--database-readonly'])==1
    assert json.loads(capsys.readouterr().out)['code']=='PREFLIGHT_UNAVAILABLE'
    engine.assert_not_called()


@pytest.mark.parametrize('fault',[None,'connect'])
def test_explicit_readonly_cli_uses_bounded_transaction_and_disposes_without_secret_output(preflight,monkeypatch,capsys,fault):
    root,*_=preflight
    monkeypatch.setenv('HOPE_DATABASE_URL','postgresql://private-secret-test-only')
    engine=MagicMock();create=Mock(return_value=engine);monkeypatch.setattr(p,'create_engine',create)
    observed=Mock(return_value=dict(state='VERIFIED',execution_authorized=False));monkeypatch.setattr(p,'inspect_preflight',observed)
    if fault=='connect':engine.connect.side_effect=RuntimeError('private-secret-test-only')
    assert p.main([SHA,'--root',str(root),'--database-readonly'])==(0 if fault is None else 1)
    output=capsys.readouterr().out
    assert 'private' not in output and json.loads(output)['execution_authorized'] is False
    assert create.call_args.kwargs['isolation_level']=='REPEATABLE READ'
    assert 'default_transaction_read_only=on' in create.call_args.kwargs['connect_args']['options']
    assert create.call_args.kwargs['connect_args']['connect_timeout']<=3
    engine.dispose.assert_called_once()


@pytest.mark.skipif(os.name!='posix',reason='Native POSIX migration storage trust')
@pytest.mark.parametrize('fault',['directory_write','file_write','symlink','hardlink','directory_link','sequence'])
def test_native_migration_storage_cannot_borrow_unsafe_or_substituted_files(tmp_path,monkeypatch,fault):
    monkeypatch.setattr(p.runtime,'OWNER',os.geteuid())
    directory=tmp_path/'migrations';directory.mkdir();path=directory/'001_first.sql';path.write_text('SELECT 1;')
    if fault=='directory_write':directory.chmod(0o777)
    elif fault=='file_write':path.chmod(0o666)
    elif fault=='symlink':path.rename(tmp_path/'peer.sql');path.symlink_to(tmp_path/'peer.sql')
    elif fault=='hardlink':os.link(path,tmp_path/'peer.sql')
    elif fault=='directory_link':directory.rename(tmp_path/'peer');directory.symlink_to(tmp_path/'peer',target_is_directory=True)
    else:path.rename(directory/'002_gap.sql')
    with pytest.raises(RuntimeError):p.inventory(directory)
