"""Interpreter and kernel process identity, independently of service liveness."""
import io
import os
import subprocess
import sys
import venv
from pathlib import Path
from unittest.mock import Mock

import pytest

from hope.api import dashboard_activation as m
from hope.api.runtime_identity import verify_interpreter,verify_listener
from test_dashboard_only_activation import OLD,NEW,setup
from test_activation_guard import guard_platform


def test_shared_request_gate_rejects_wrong_virtual_environment(setup,monkeypatch):
    layout,host=setup
    m.activate(layout,host,target=NEW,shared=OLD,ci_run=123,ci_attempt=1)
    monkeypatch.setattr(m,"_interpreter_identity",verify_interpreter)
    monkeypatch.setattr(m.sys,"executable",str(layout.root/"substitute/bin/python"))
    monkeypatch.setattr(m.sys,"prefix",str(layout.root/"substitute"))
    with pytest.raises(RuntimeError,match="RUNTIME_IDENTITY_MISMATCH"):
        m.runtime_gate(layout,NEW)


def test_shared_listener_cannot_use_other_venv_with_same_base_interpreter(setup,monkeypatch):
    layout,_=setup;host=m.Host(layout)
    layout.python.parent.mkdir(parents=True);layout.python.write_text("python")
    host.show=Mock(return_value={"MainPID":"321"})
    host.run=Mock(return_value='LISTEN 0 128 127.0.0.1:8788 0.0.0.0:* users:(("python",pid=321,fd=3))')
    resolve=Path.resolve;opened=Path.open;path_stat=Path.stat
    monkeypatch.setattr(Path,"resolve",lambda path,*a,**k:
        resolve(layout.python,*a,**k) if path.as_posix()=="/proc/321/exe" else resolve(path,*a,**k))
    monkeypatch.setattr(Path,"open",lambda path,*a,**k:
        io.BytesIO(b"\0".join((b"/substitute/bin/python",b"-m",b"hope.api.dashboard_server",b"--port",b"8788"))+b"\0")
        if path.as_posix()=="/proc/321/cmdline" else opened(path,*a,**k))
    monkeypatch.setattr(Path,"stat",lambda path,*a,**k:
        path_stat(layout.python,*a,**k) if path.as_posix()=="/proc/321/exe" else path_stat(path,*a,**k))
    with pytest.raises(RuntimeError,match="LISTENER_(ENVIRONMENT|COMMAND)_MISMATCH"):
        host.listener()


@pytest.mark.parametrize("mode",["shared","release"])
@pytest.mark.parametrize("changed",["executable","prefix",None])
def test_interpreter_contract_binds_both_path_and_prefix(tmp_path,monkeypatch,mode,changed):
    expected=tmp_path/("venv" if mode=="shared" else "release-venvs/"+NEW)/"bin/python"
    monkeypatch.setattr(sys,"executable",str(tmp_path/"substitute/bin/python" if changed=="executable" else expected))
    monkeypatch.setattr(sys,"prefix",str(tmp_path/"substitute" if changed=="prefix" else expected.parent.parent))
    if changed:
        with pytest.raises(RuntimeError,match="^DASHBOARD_RUNTIME_IDENTITY_MISMATCH$"):verify_interpreter(expected)
    else:verify_interpreter(expected)


def test_real_separate_venvs_sharing_base_python_are_not_interchangeable(tmp_path_factory):
    root=tmp_path_factory.mktemp("venv-id")
    first,second=root/"a",root/"b"
    for environment in (first,second):venv.EnvBuilder(with_pip=False,symlinks=os.name=="posix").create(environment)
    binary="Scripts/python.exe" if os.name=="nt" else "bin/python"
    selected=first/binary
    # The production Linux contract uses bin/python; on Windows exercise the
    # same identity primitive with an equivalent two-level launcher layout.
    if os.name=="nt":
        first_prefix=first;second_prefix=second
    code=("import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
        "from hope.api.runtime_identity import verify_interpreter;"
        "print(sys._base_executable,flush=True);verify_interpreter(Path(sys.argv[2]))")
    source=Path(__file__).parents[2]/"src"
    success=subprocess.run([str(selected),"-B","-s","-P","-c",code,str(source),str(selected)],
        capture_output=True,text=True,timeout=20)
    wrong=subprocess.run([str(second/binary),"-B","-s","-P","-c",code,str(source),str(selected)],
        capture_output=True,text=True,timeout=20)
    assert success.returncode==0,success.stderr
    assert wrong.returncode!=0 and "DASHBOARD_RUNTIME_IDENTITY_MISMATCH" in wrong.stderr
    assert success.stdout.strip()==wrong.stdout.strip()


@pytest.mark.skipif(os.name!="posix",reason="Native proc-style executable links; Linux CI")
@pytest.mark.parametrize("managed",[False,True])
@pytest.mark.parametrize("changed",[None,"venv","module","port","flags","oversize","unterminated","generation","initial_generation","binary","replacement"])
def test_listener_binds_one_generation_to_environment_module_and_private_port(tmp_path,managed,changed,monkeypatch):
    python=tmp_path/"venv/bin/python";python.parent.mkdir(parents=True);python.write_bytes(b"python")
    proc=tmp_path/"proc";process=proc/"321";process.mkdir(parents=True)
    (process/"exe").symlink_to(python)
    arguments=[os.fsencode(python)]
    if managed:arguments.extend((b"-B",b"-s",b"-P"))
    arguments.extend((b"-m",b"hope.api.dashboard_server",b"--port",b"8788"))
    if changed=="venv":arguments[0]=b"/other/bin/python"
    elif changed=="module":arguments[arguments.index(b"hope.api.dashboard_server")]=b"other.module"
    elif changed=="port":arguments[-1]=b"8787"
    elif changed=="flags":arguments.insert(1,b"-E")
    command=b"\0".join(arguments)+b"\0"
    if changed=="oversize":command=b"x"*65537
    elif changed=="unterminated":command=command[:-1]
    (process/"cmdline").write_bytes(command)
    identity=Mock(side_effect=[("boot","10"),("boot","20" if changed=="generation" else "10")])
    if changed=="binary":
        other=tmp_path/"other";other.write_bytes(b"python")
        (process/"exe").unlink();(process/"exe").symlink_to(other)
    if changed=="replacement":
        opened=Path.open
        def replace(path,*a,**k):
            if path==process/"cmdline":
                python.rename(python.with_name("retained"));python.write_bytes(b"python")
            return opened(path,*a,**k)
        monkeypatch.setattr(Path,"open",replace)
    kwargs=dict(identity=identity,managed=managed,proc=proc,
        expected_identity=("boot","wrong" if changed=="initial_generation" else "10"))
    if changed:
        with pytest.raises(RuntimeError,match="^DASHBOARD_LISTENER_"):verify_listener(321,python,**kwargs)
    else:verify_listener(321,python,**kwargs)


def test_listener_rechecks_systemd_pid_before_accepting_host_identity(setup,monkeypatch):
    layout,_=setup;host=m.Host(layout)
    host.show=Mock(side_effect=[{"MainPID":"321"},{"MainPID":"322"}])
    host.run=Mock(return_value='LISTEN 0 128 127.0.0.1:8788 0.0.0.0:* users:(("python",pid=321,fd=3))')
    check=Mock();monkeypatch.setattr(m,"verify_listener",check)
    with pytest.raises(RuntimeError,match="^DASHBOARD_LISTENER_PROCESS_CHANGED$"):host.listener()
    assert check.call_args.kwargs["expected_identity"]==m.operations._process_identity(321)
    assert [call.args[0] for call in host.show.call_args_list]==[m.WEB,m.WEB]
