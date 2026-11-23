"""Read-only surface tests; no admission/financial/provider acceptance is supplied."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from hope.api import dashboard_server as module
from hope.api.dashboard_server import DashboardHTTPServer
from test_dashboard_server import request
from test_market_observation import NOW,COMMIT,capture


@pytest.fixture(autouse=True)
def native_file_boundary(monkeypatch):
    if os.name!='posix':
        # Windows lstat creation-time differs from fstat ChangeTime. Model only
        # that native boundary; Linux CI executes the actual inode/permission reader.
        def read(path,limit):
            b=Path(path).read_bytes()
            if len(b)>limit:raise ValueError('TEST_NATIVE_SIZE')
            return b
        def authenticated(path,limit,*,expected_sha256):
            b=read(path,limit)
            if not isinstance(expected_sha256,str) or hashlib.sha256(b).hexdigest()!=expected_sha256:
                raise ValueError('TEST_NATIVE_DIGEST')
            return b
        monkeypatch.setattr(module,'bounded_regular_read',read)
        monkeypatch.setattr(module,'authenticated_bounded_regular_read',authenticated)


def current_capture():
    # Explicit synthetic fixture clock only; genuine receipt clocks are never rewritten.
    value=json.loads(capture());stamp=datetime.now(timezone.utc).isoformat()
    value['generated_at']=stamp
    for r in value['replies']:r['received_at']=stamp
    return json.dumps(value).encode()


@contextmanager
def server(tmp_path,payload=None,*,activation_view=None):
    html=tmp_path/'preview.html';html.write_text('preview',encoding='utf-8')
    market=tmp_path/'market.html';market.write_text('market',encoding='utf-8')
    feed=tmp_path/'capture.json'
    if payload is not None:feed.write_bytes(payload)
    for p in tmp_path.iterdir():p.chmod(0o600)
    with DashboardHTTPServer(0,html,tmp_path/'snapshot.json',deployed_commit=COMMIT,
            market_capture=feed,activation_view=activation_view) as s:
        t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
        try:yield s
        finally:s.shutdown();t.join(timeout=5)


def test_private_market_transport_does_not_depend_on_database_or_change_authority(tmp_path):
    with server(tmp_path,current_capture()) as s:
        status,headers,body=request(s,'/market-observations.json');v=json.loads(body)
        assert status==200 and v['watchlist'][10]['quote']['price']=='102'
        assert v['execution_authorized'] is v['live_enabled'] is v['independent_verified'] is False
        assert 'no-store' in headers['Cache-Control'] and "connect-src 'self'" in headers['Content-Security-Policy']
        assert json.loads(request(s,'/status.json')[2])['data_feed']=='UNAVAILABLE'
        assert request(s,'/market.html')[0]==200


@pytest.mark.parametrize('payload',[None,b'{}',b'{"execution_authorized":true}',b'NEVER_DISCLOSE',b'x'*(2*1024*1024+1)],ids=['missing','empty','authority','malformed','oversize'])
def test_missing_or_invalid_capture_returns_no_sensitive_data(tmp_path,payload):
    with server(tmp_path,payload) as s:
        status,_,body=request(s,'/market-observations.json');v=json.loads(body)
        assert status==200 and v['state']=='UNAVAILABLE'
        assert v['execution_authorized'] is v['live_enabled'] is False
        assert 'NEVER_DISCLOSE' not in body.decode() and str(tmp_path) not in body.decode()


@pytest.mark.parametrize('method',['POST','PUT','DELETE','HEAD'])
def test_market_surface_rejects_all_mutations(tmp_path,method):
    with server(tmp_path,current_capture()) as s:
        assert request(s,'/market-observations.json',method=method)[0]==405
        assert request(s,'/market.html',method=method)[0]==405


def test_market_surface_rebinding_and_arbitrary_paths_are_closed(tmp_path):
    with server(tmp_path,current_capture()) as s:
        assert request(s,'/market-observations.json',host='evil.invalid')[0]==403
        assert request(s,'/market.html?file=capture.json')[0]==404
        assert request(s,'/capture.json')[0]==404
        assert request(s,'/../market.html')[0]==404


def test_protected_market_page_requires_distinct_independent_asset_digest(tmp_path):
    @contextmanager
    def gate():yield {'preview_sha256':hashlib.sha256(b'preview').hexdigest()}
    with server(tmp_path,current_capture(),activation_view=gate) as s:
        assert request(s,'/')[0]==200 and request(s,'/market.html')[0]==503


def test_protected_market_asset_exact_digest_and_substitution_denial(tmp_path):
    @contextmanager
    def gate():yield {'preview_sha256':hashlib.sha256(b'preview').hexdigest(),
                     'market_preview_sha256':hashlib.sha256(b'market').hexdigest()}
    with server(tmp_path,current_capture(),activation_view=gate) as s:
        assert request(s,'/market.html')[0]==200
        (tmp_path/'market.html').write_text('mutate')
        assert request(s,'/market.html')[0]==503


@pytest.mark.skipif(os.name!='posix',reason='Actual symlink/inode denial is qualified on native Linux CI')
def test_market_capture_symlink_is_not_followed(tmp_path):
    with server(tmp_path,current_capture()) as s:
        source=tmp_path/'original';(tmp_path/'capture.json').rename(source)
        (tmp_path/'capture.json').symlink_to(source)
        assert json.loads(request(s,'/market-observations.json')[2])['state']=='UNAVAILABLE'


def test_exact_shipped_market_javascript_behaviors():
    node=os.environ.get('HOPE_NODE') or shutil.which('node');assert node,'CI requires Node 22'
    root=Path(__file__).resolve().parents[2]
    assert (root/'docs/dashboard/preview.html').stat().st_size<=65536
    assert (root/'docs/dashboard/market.html').stat().st_size<=65536
    result=subprocess.run([node,str(root/'tests/browser/market_behavior.cjs'),str(root/'docs/dashboard/market.html')],
        capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert 'market observation behavior passed' in result.stdout
