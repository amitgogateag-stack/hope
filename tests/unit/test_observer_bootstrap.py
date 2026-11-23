"""Observer bootstrap checks with explicit mocked runtime/host acceptance only."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from hope.api import runtime_bootstrap as module

SHA='2'*40

def ready(tmp_path,monkeypatch):
    from hope.api import dashboard_activation, dashboard_snapshot_refresh
    release=tmp_path/'releases'/SHA;release.mkdir(parents=True)
    monkeypatch.setattr(module,'sys',SimpleNamespace(flags=SimpleNamespace(isolated=True,no_site=False),version_info=(3,13)))
    authenticate=Mock(return_value=(release/'python',{'manifest_sha256':'a'*64}))
    monkeypatch.setattr(module,'authenticate_control',authenticate)
    interpreter=Mock();monkeypatch.setattr(module,'verify_interpreter',interpreter)
    host=Mock();monkeypatch.setattr(dashboard_activation,'Host',host)
    refresh=Mock(return_value=0);monkeypatch.setattr(dashboard_snapshot_refresh,'main',refresh)
    return release,authenticate,interpreter,host,refresh


def test_observer_handoff_passes_only_observer_credential_and_fixed_dispatch(tmp_path,monkeypatch):
    release=tmp_path/'releases'/SHA
    monkeypatch.setattr(module,'authenticate_control',Mock(return_value=(release/'python',{'manifest_sha256':'a'*64})))
    monkeypatch.setenv('HOPE_DATABASE_URL','postgresql://explicit-test-reader')
    monkeypatch.setenv('GH_TOKEN','never-forward');monkeypatch.setenv('LD_PRELOAD','never-forward')
    execute=Mock(return_value=17)
    assert module.handoff(release,['--deployed-commit',SHA],'release',observer_refresh=True,execute=execute)==17
    _,argv,environment=execute.call_args.args
    assert argv[argv.index('--control-manifest')+2:]==['--observer-refresh','--deployed-commit',SHA]
    assert environment=={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','LC_ALL':'C','HOPE_DATABASE_URL':'postgresql://explicit-test-reader'}


def test_os_stage_preserves_observer_dispatch_for_reauthenticated_stage(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'sys',SimpleNamespace(flags=SimpleNamespace(isolated=True,no_site=True)))
    handoff=Mock(return_value=8);monkeypatch.setattr(module,'handoff',handoff)
    release=tmp_path/'releases'/SHA
    assert module.main(['--observer-refresh','--control-runtime','release','--deployed-commit',SHA],release=release)==8
    handoff.assert_called_once_with(release,['--deployed-commit',SHA],'release',observer_refresh=True)


def test_runtime_interpreter_and_paper_isolation_precede_fixed_observer_dispatch(tmp_path,monkeypatch):
    release,authenticate,interpreter,host,refresh=ready(tmp_path,monkeypatch)
    order=Mock();order.attach_mock(authenticate,'authenticate');order.attach_mock(interpreter,'interpreter')
    order.attach_mock(host.return_value.paper_safe,'paper');order.attach_mock(refresh,'refresh')
    arguments=['--deployed-commit',SHA,'--output','snapshot','--status','receipt']
    assert module.main(['--observer-refresh','--control-manifest','a'*64,*arguments],release=release)==0
    assert [call[0] for call in order.mock_calls]==['authenticate','interpreter','paper','refresh']
    refresh.assert_called_once_with(arguments)
    host.return_value.control.assert_not_called()


@pytest.mark.parametrize('failure',['runtime','interpreter','paper'])
def test_trust_or_paper_failure_never_dispatches_database_refresh(tmp_path,monkeypatch,capsys,failure):
    release,authenticate,interpreter,host,refresh=ready(tmp_path,monkeypatch)
    target={'runtime':authenticate,'interpreter':interpreter,'paper':host.return_value.paper_safe}[failure]
    target.side_effect=RuntimeError('private-host-or-credential')
    with pytest.raises(SystemExit) as error:
        module.main(['--observer-refresh','--control-manifest','a'*64,'--deployed-commit',SHA],release=release)
    assert error.value.code==2;refresh.assert_not_called()
    assert capsys.readouterr().err=='DASHBOARD_BOOTSTRAP_BLOCKED REVIEW_SOURCE_AND_RUNTIME_MANIFEST\n'
    host.return_value.control.assert_not_called()


@pytest.mark.parametrize('arguments',[
    [],['--deployed-commit','3'*40],['--deployed-commit',SHA,'--deployed-commit',SHA],
    ['--deployed-commit='+SHA,'--interval-seconds=60'],['--deployed-commit',SHA,'--interval-seconds','60'],
])
def test_missing_ambiguous_or_unbound_release_and_long_lived_loop_are_refused(tmp_path,monkeypatch,arguments):
    release,_,_,host,refresh=ready(tmp_path,monkeypatch)
    with pytest.raises(SystemExit):
        module.main(['--observer-refresh','--control-manifest','a'*64,*arguments],release=release)
    refresh.assert_not_called();host.assert_not_called()


@pytest.mark.parametrize('arguments',[
    ['--expected-databa','lab'],['--interval','60'],['--arbitrary-factory','worker:run'],
])
def test_observer_cli_does_not_accept_abbreviation_or_factory_dispatch(monkeypatch,arguments):
    from hope.api import dashboard_snapshot_refresh
    refresh=Mock();monkeypatch.setattr(dashboard_snapshot_refresh,'refresh_once',refresh)
    common=['--output','snapshot','--status','receipt','--deployed-commit',SHA,
        '--expected-database','lab','--expected-reader','reader','--expected-database-identity','a'*64]
    with pytest.raises(SystemExit) as error:dashboard_snapshot_refresh.main([*common,*arguments])
    assert error.value.code==2;refresh.assert_not_called()
