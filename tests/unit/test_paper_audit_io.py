"""Resource/race regressions for privileged read-only diagnostics."""
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

from hope.api import paper_absence_audit as module
from hope.api import dashboard_activation as activation
from hope.api import diagnostic_io as io


def test_replacement_after_lstat_cannot_supply_different_file_contents(tmp_path,monkeypatch):
    path=tmp_path/"job";path.write_text("reviewed")
    replacement=tmp_path/"replacement";replacement.write_text("unreviewed")
    original=Path.lstat
    def race(candidate,*args,**kwargs):
        result=original(candidate,*args,**kwargs)
        if candidate==path and replacement.exists():replacement.replace(path)
        return result
    monkeypatch.setattr(Path,"lstat",race)
    with pytest.raises((OSError,ValueError)):module._read(path)


@pytest.mark.skipif(os.name!="posix",reason="Native no-follow descriptor proof runs in Linux CI")
def test_replacement_after_lstat_cannot_follow_a_symlink(tmp_path,monkeypatch):
    path=tmp_path/"job";path.write_text("reviewed")
    outside=tmp_path/"outside";outside.write_text("must-not-read")
    original=Path.lstat
    def race(candidate,*args,**kwargs):
        result=original(candidate,*args,**kwargs)
        if candidate==path:
            path.unlink();path.symlink_to(outside)
        return result
    monkeypatch.setattr(Path,"lstat",race)
    with pytest.raises((OSError,ValueError)):
        module._read(path)


def test_changed_contents_during_read_are_rejected_and_descriptor_is_closed(tmp_path,monkeypatch):
    path=tmp_path/"job";path.write_bytes(b"reviewed")
    original=module.os.read;descriptors=[]
    def race(fd,size):
        descriptors.append(fd);result=original(fd,size)
        if result:path.write_bytes(b"unreviewed-longer")
        return result
    monkeypatch.setattr(module.os,"read",race)
    with pytest.raises(ValueError,match="FILE_CHANGED"):module._read(path)
    with pytest.raises(OSError):os.fstat(descriptors[0])


@pytest.mark.skipif(os.name!="posix",reason="Native FIFO nonblocking proof runs in Linux CI")
def test_replacement_with_fifo_does_not_block_before_type_rejection(tmp_path,monkeypatch):
    path=tmp_path/"job";path.write_text("reviewed")
    original=Path.lstat
    def race(candidate,*args,**kwargs):
        result=original(candidate,*args,**kwargs)
        if candidate==path:path.unlink();os.mkfifo(path)
        return result
    monkeypatch.setattr(Path,"lstat",race)
    started=time.monotonic()
    with pytest.raises(ValueError):module._read(path)
    assert time.monotonic()-started<1


def test_huge_scheduler_iterator_is_cut_off_before_materialization(tmp_path,monkeypatch):
    class Entries:
        consumed=0
        def __enter__(self):return self
        def __exit__(self,*args):self.closed=True
        def __iter__(self):return self
        def __next__(self):
            self.consumed+=1
            if self.consumed>5:raise AssertionError("eager unbounded enumeration")
            return type("Entry",(),{"path":str(tmp_path/str(self.consumed))})()
    entries=Entries();monkeypatch.setattr(module.os,"scandir",lambda path:entries)
    monkeypatch.setattr(module,"LIMIT",4)
    with pytest.raises(ValueError,match="LIMIT"):module.schedule_inventory((tmp_path,))
    assert entries.consumed==4 and entries.closed


def test_unbounded_top_level_selection_is_not_materialized(tmp_path,monkeypatch):
    monkeypatch.setattr(module,"LIMIT",2)
    def paths():
        for i in range(4):
            if i==3:pytest.fail("selection overconsumed")
            yield tmp_path/str(i)
    with pytest.raises(ValueError,match="LIMIT"):module.schedule_inventory(paths())


@pytest.mark.parametrize("args",[("systemctl","start","hope-paper.service"),
    ("systemctl","daemon-reload"),("/tmp/systemctl","list-units"),
    ("systemctl","show","-evil.service","--property=Id"),
    ("systemctl","show","worker.service","--property=Environment"),
    ("systemctl","list-units","--host=remote")])
def test_diagnostic_adapter_cannot_mutate_services_or_expand_its_scope(monkeypatch,args):
    run=Mock();monkeypatch.setattr(activation,"bounded_capture",run)
    with pytest.raises(RuntimeError,match="COMMAND_DENIED"):activation.Host(activation.Layout()).audit_run(*args)
    run.assert_not_called()


def test_diagnostic_adapter_uses_fixed_binary_environment_and_limits(monkeypatch):
    monkeypatch.setenv("PATH","/hostile");monkeypatch.setenv("GH_TOKEN","do-not-inherit")
    run=Mock(return_value="[]");monkeypatch.setattr(activation,"bounded_capture",run)
    args=("systemctl","list-units","--all","--no-pager","--output=json")
    assert activation.Host(activation.Layout()).audit_run(*args,timeout=2,max_output=128)=="[]"
    assert run.call_args.args[0]==("/usr/bin/systemctl",*args[1:])
    assert run.call_args.kwargs==dict(timeout=2,max_output=128,env=activation.SAFE_ENVIRONMENT)


@pytest.mark.parametrize("code,complete",[(0,True),(4,True),(4,False)])
def test_canonical_probe_uses_bounded_adapter_and_preserves_absence_block(monkeypatch,code,complete):
    from test_paper_absence_audit import ABSENT,properties
    raw=properties(ABSENT) if complete else "LoadState=not-found\n"
    capture=Mock(return_value=raw)
    if code:capture.side_effect=subprocess.CalledProcessError(code,"systemctl",output=raw)
    monkeypatch.setattr(activation,"bounded_capture",capture)
    host=activation.Host(activation.Layout());host.run=Mock(side_effect=AssertionError("unbounded fallback"))
    if complete:
        assert module.unit_state(host.paper_properties(run=host.audit_run))=="ABSENT_REQUIRES_REVIEW"
    else:
        with pytest.raises(RuntimeError,match="UNIT_UNVERIFIED"):host.paper_properties(run=host.audit_run)
    host.run.assert_not_called()
    assert capture.call_args.kwargs["max_output"]==module.BYTE_LIMIT
    assert capture.call_args.kwargs["timeout"]==10


def test_systemd_commands_share_one_deadline_and_stream_output_caps(monkeypatch):
    from test_paper_absence_audit import systemd_run
    clock=[0.0];monkeypatch.setattr(module.time,"monotonic",lambda:clock[0])
    callback=systemd_run(command="/usr/bin/true");calls=[]
    def run(*args,**kwargs):
        calls.append(kwargs);clock[0]+=2
        return callback(*args)
    assert module.systemd_inventory(run)==(1,0,0)
    assert [c["timeout"] for c in calls]==[10,8,6]
    assert [c["max_output"] for c in calls]==[4*1024*1024,4*1024*1024,module.BYTE_LIMIT]


@pytest.mark.skipif(os.name!="posix",reason="Native process-group capture runs in Linux CI")
@pytest.mark.parametrize("source,limit,expected",[
    ("print('ok',end='')",2,"ok"),
    ("import os;os.write(2,b'secret'*1000000);print('ok',end='')",2,"ok"),
])
def test_native_capture_accepts_exact_bound_and_discards_stderr(source,limit,expected):
    assert io.bounded_capture([sys.executable,"-c",source],timeout=5,max_output=limit,
        env=activation.SAFE_ENVIRONMENT)==expected


@pytest.mark.skipif(os.name!="posix",reason="Native process-group capture runs in Linux CI")
@pytest.mark.parametrize("source,code",[
    ("import os;os.write(1,b'secret'*1000000)","OUTPUT_LIMIT"),
    ("import time;time.sleep(60)","TIMEOUT"),
    ("import os,time;os.close(1);time.sleep(60)","TIMEOUT"),
])
def test_flooding_or_hung_children_are_killed_reaped_and_closed(monkeypatch,source,code):
    factory=io.subprocess.Popen;children=[]
    def record(*args,**kwargs):
        child=factory(*args,**kwargs);children.append(child);return child
    monkeypatch.setattr(io.subprocess,"Popen",record)
    started=time.monotonic()
    with pytest.raises(RuntimeError,match="DIAGNOSTIC_"+code) as error:
        io.bounded_capture([sys.executable,"-c",source],timeout=1,max_output=128,env=activation.SAFE_ENVIRONMENT)
    assert time.monotonic()-started<3 and "secret" not in str(error.value)
    assert children[0].returncode is not None and children[0].stdout.closed


@pytest.mark.skipif(os.name!="posix",reason="Native inherited pipe/session proof runs in Linux CI")
def test_exited_leader_with_inherited_stdout_does_not_escape_deadline(tmp_path):
    pidfile=tmp_path/"pid"
    source="import subprocess,sys,pathlib;child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);pathlib.Path(sys.argv[1]).write_text(str(child.pid))"
    started=time.monotonic()
    with pytest.raises(RuntimeError,match="TIMEOUT"):
        io.bounded_capture([sys.executable,"-c",source,str(pidfile)],timeout=2,max_output=128,env=activation.SAFE_ENVIRONMENT)
    assert time.monotonic()-started<4
    pid=pidfile.read_text();stat_path=Path("/proc")/pid/"stat"
    deadline=time.monotonic()+1
    while stat_path.exists():
        try:state=stat_path.read_text().rsplit(")",1)[1].split()[0]
        except FileNotFoundError:break
        if state=="Z":break
        assert time.monotonic()<deadline,"descendant survived process-group cleanup"
        time.sleep(.01)


@pytest.mark.skipif(os.name!="posix",reason="Native capture error protocol runs in Linux CI")
@pytest.mark.parametrize("source,kind",[("import sys;print('bounded');sys.exit(4)",subprocess.CalledProcessError),
    ("import os;os.write(1,b'\\xff')",UnicodeError)])
def test_nonzero_and_invalid_encoding_never_become_clean_output(source,kind):
    with pytest.raises(kind):io.bounded_capture([sys.executable,"-c",source],timeout=5,max_output=128,env=activation.SAFE_ENVIRONMENT)


@pytest.mark.parametrize("timeout,limit",[(0,1),(-1,1),(21,1),(1,0),(1,4*1024*1024+1)])
def test_invalid_capture_limits_never_start_a_child(monkeypatch,timeout,limit):
    from types import SimpleNamespace
    monkeypatch.setattr(io,"os",SimpleNamespace(name="posix"))
    run=Mock();monkeypatch.setattr(io.subprocess,"Popen",run)
    with pytest.raises(ValueError):io.bounded_capture(["unused"],timeout=timeout,max_output=limit,env={})
    run.assert_not_called()


@pytest.mark.skipif(os.name!="posix",reason="Real procfs descriptor metadata runs in Linux CI")
def test_descriptor_reader_supports_zero_sized_procfs_files():
    value=module._read(Path("/proc/self/stat"))
    assert value.startswith(str(os.getpid()).encode()+b" (")


@pytest.mark.skipif(os.name!="posix",reason="Native wait/PID-reuse cleanup proof runs in Linux CI")
@pytest.mark.parametrize("source",["print('ok')","import sys;sys.exit(4)","import os;os.write(1,b'\\xff')"])
def test_reaped_leader_is_never_targeted_by_cleanup(monkeypatch,source):
    kill=Mock(side_effect=AssertionError("attempted to signal a reaped PID"))
    monkeypatch.setattr(io.os,"killpg",kill)
    try:io.bounded_capture([sys.executable,"-c",source],timeout=5,max_output=128,env=activation.SAFE_ENVIRONMENT)
    except (subprocess.CalledProcessError,UnicodeError):pass
    kill.assert_not_called()
