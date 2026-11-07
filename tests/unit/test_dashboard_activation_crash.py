"""Native Linux SIGKILL, flock and reboot-gate evidence; no AWS/systemd mutation."""
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler,build_opener

import pytest

from hope.api import dashboard_activation as module
from hope.api import operations_deployment as operations
from test_dashboard_only_activation import OLD,NEW,FakeHost


@pytest.mark.skipif(sys.platform!="linux",reason="Native Linux crash/fsync/symlink/flock; exercised in Linux CI")
@pytest.mark.parametrize("crash",["PREPARED","CONFIGS","POINTER","VERIFYING","STARTED","COMMITTED"])
@pytest.mark.parametrize("manifest_bound",[False,True])
def test_sigkill_fences_boot_and_explicit_rollback_restores_original(tmp_path,monkeypatch,crash,manifest_bound):
    monkeypatch.setattr(module,"OWNER",os.getuid())
    monkeypatch.setattr(module,"_code_identity",lambda *a:None)
    monkeypatch.setattr(module,"_interpreter_identity",lambda *a:None)
    def approved(root,sha,mode,**kwargs):
        return dict(schema="hope.runtime-integrity.v1",state="VERIFIED",code="MANIFEST_AND_RUNTIME_MATCH",
            release_commit=sha,runtime_mode=mode,manifest_sha256=("a" if sha==OLD else "b")*64,
            inventory_sha256="c"*64,process_sha256=None,file_count=1,execution_authorized=False,provenance_state="VERIFIED",provenance_sha256="e"*64,build_state="VERIFIED",build_sha256="f"*64,builder_id="c"*64)
    monkeypatch.setattr(module,"attest_runtime",approved)
    layout=module.Layout(root=tmp_path,units=tmp_path/"units")
    layout.units.mkdir()
    for sha in (OLD,NEW):(tmp_path/"releases"/sha).mkdir(parents=True)
    os.symlink(tmp_path/"releases"/OLD,tmp_path/"current",target_is_directory=True)
    ready=tmp_path/"ready"
    code='''
import os,sys,time
from pathlib import Path
from hope.api import dashboard_activation as m
from test_dashboard_only_activation import OLD,NEW,FakeHost
m.OWNER=os.getuid()
m._code_identity=lambda *a:None
m._interpreter_identity=lambda *a:None
def approved(root,sha,mode,**kwargs):
    return dict(schema="hope.runtime-integrity.v1",state="VERIFIED",code="MANIFEST_AND_RUNTIME_MATCH",
        release_commit=sha,runtime_mode=mode,manifest_sha256=("a" if sha==OLD else "b")*64,
        inventory_sha256="c"*64,process_sha256=None,file_count=1,execution_authorized=False,provenance_state="VERIFIED",provenance_sha256="e"*64,build_state="VERIFIED",build_sha256="f"*64,builder_id="c"*64)
m.attest_runtime=approved
root=Path(sys.argv[1]);phase=sys.argv[2]
layout=m.Layout(root=root,units=root/'units')
def pause():
    (root/'ready').write_text('ready')
    time.sleep(60)
save=m._save
def saved(path,value,*args):
    result=save(path,value,*args)
    if path==layout.journal and value.get('phase')==phase:pause()
    return result
m._save=saved
if phase in {'CONFIGS','POINTER'}:
    name='_install_configs' if phase=='CONFIGS' else '_point'
    original=getattr(m,name)
    def hooked(*args,**kwargs):
        result=original(*args,**kwargs);pause();return result
    setattr(m,name,hooked)
host=FakeHost()
if sys.argv[3]=='bound':host.runtime_manifests={OLD:'a'*64,NEW:'b'*64}
if phase=='STARTED':host.verify=lambda *a,**k:pause()
m.activate(layout,host,target=NEW,shared=OLD,ci_run=123,ci_attempt=1)
'''
    env=dict(os.environ)
    env["PYTHONPATH"]=os.pathsep.join([str(Path(__file__).parents[2]/"src"),str(Path(__file__).parent)])
    child=subprocess.Popen([sys.executable,"-B","-c",code,str(tmp_path),crash,"bound" if manifest_bound else "legacy"],env=env,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    web=None
    try:
        deadline=time.monotonic()+15
        while not ready.exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(.05)
        assert ready.exists(),child.communicate(timeout=1)
        with pytest.raises(operations.DeploymentError,match="LOCKED"):
            module.activate(layout,FakeHost(),target=NEW,shared=OLD,ci_run=123,ci_attempt=1)
        if crash=="STARTED":
            source=Path(__file__).parents[2]/"src/hope"
            shutil.copytree(source,tmp_path/"releases"/NEW/"src/hope",
                ignore=shutil.ignore_patterns("__pycache__"))
            webcode='''
import os,sys
from pathlib import Path
from hope.api import dashboard_activation as m
from hope.api.dashboard_server import DashboardHTTPServer
m.OWNER=os.getuid();root=Path(sys.argv[1]);layout=m.Layout(root=root,units=root/'units')
m._interpreter_identity=lambda *a:None
m.attest_runtime=lambda root,sha,mode,**kwargs:dict(schema="hope.runtime-integrity.v1",state="VERIFIED",
    code="MANIFEST_AND_RUNTIME_MATCH",release_commit=sha,runtime_mode=mode,manifest_sha256='b'*64,
    inventory_sha256="c"*64,process_sha256=None,file_count=1,execution_authorized=False,provenance_state="VERIFIED",provenance_sha256="e"*64,build_state="VERIFIED",build_sha256="f"*64,builder_id="c"*64)
with DashboardHTTPServer(0,root/'page',root/'snapshot',deployed_commit=sys.argv[2],
        activation_check=lambda:m.runtime_gate(layout,sys.argv[2])) as server:
    (root/'web-port').write_text(str(server.server_port))
    server.serve_forever()
'''
            webenv=dict(env);webenv["PYTHONPATH"]=str(tmp_path/"dashboard-current/src")
            web=subprocess.Popen([sys.executable,"-B","-c",webcode,str(tmp_path),NEW],env=webenv,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            deadline=time.monotonic()+15
            while not (tmp_path/"web-port").exists() and web.poll() is None and time.monotonic()<deadline:time.sleep(.05)
            assert (tmp_path/"web-port").exists()
            base="http://127.0.0.1:"+(tmp_path/"web-port").read_text()
            opener=build_opener(ProxyHandler({}))
            with opener.open(base+"/status.json",timeout=5) as response:assert response.status==200
        child.kill();child.wait(timeout=5)
        assert not module._owner_alive(module._record(layout))
        if crash=="COMMITTED":
            assert module.gate(layout,NEW,FakeHost())["status"]=="GATE_PASSED"
        else:
            with pytest.raises(RuntimeError):module.gate(layout,NEW,FakeHost())
            with pytest.raises(RuntimeError,match="RECOVERY_REQUIRED"):
                module.activate(layout,FakeHost(),target=NEW,shared=OLD,ci_run=123,ci_attempt=1)
        if web is not None:
            assert web.poll() is None
            with pytest.raises(HTTPError) as error:opener.open(base+"/status.json",timeout=5)
            assert error.value.code==503;error.value.close()
        module.rollback(layout,FakeHost(),target=NEW,shared=OLD)
        assert module._record(layout) is None and module._pointer(layout,"current")==OLD
        assert module._pointer(layout,"dashboard-current",optional=True) is None
    finally:
        if web is not None:
            web.kill();web.wait(timeout=5);web.communicate(timeout=5)
        if child.poll() is None:child.kill();child.wait(timeout=5)
        child.communicate(timeout=5)
