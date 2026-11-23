"""Pure preview build/export behavior; synthetic replies are not market proof."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

from hope.api.private_market_preview import build_assets, project_capture
from hope.api.market_observation import make_market_capture


def git(repo, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    return subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, *args],
                          cwd=repo, env=env, check=True, capture_output=True).stdout.decode().strip()


def test_build_is_reproducible_and_uses_fixed_committed_bytes(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init')
    files = {'docs/dashboard/preview.html': b'<html>Database</html>\n',
             'docs/dashboard/market.html': b'<html>Market</html>\n',
             'deploy/operations/private_market_preview_install.py': b'"""fixture, never executed"""\n'}
    for path, b in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
    git(repo, 'add', '-f', *files)
    git(repo, '-c', 'user.name=Disposable', '-c', 'user.email=disposable@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Disposable fixed objects')
    head = git(repo, 'rev-parse', 'HEAD')
    try:
        a = build_assets(repo, head)
    except subprocess.CalledProcessError as exc:
        pytest.fail(repr(exc.stderr) + "\nTREE: " + git(repo, "ls-tree", "-r", "--name-only", head))
    (repo / 'docs/dashboard/market.html').write_bytes(b'UNCOMMITTED CHANGED BYTES')
    assert build_assets(repo, head) == a
    with zipfile.ZipFile(io.BytesIO(a)) as z:
        assert set(z.namelist()) == {'index.html', 'market.html', 'preview_install.py', 'ASSETS.json'}
        manifest = json.loads(z.read('ASSETS.json'))
        assert manifest['source_commit'] == head
        assert manifest['execution_authorized'] is manifest['runtime_verified'] is manifest['production_admission'] is False
        assert z.read('market.html') == files['docs/dashboard/market.html']
        for name, expected in manifest['files'].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == expected['sha256']
            assert len(z.read(name)) == expected['bytes']

    # A committed symlink object must not be packaged as an HTML file, even when
    # its blob bytes happen to contain valid HTML. This does not require Windows
    # symlink privileges and exercises real Git index/object modes.
    blob = git(repo, 'rev-parse', head + ':docs/dashboard/market.html')
    git(repo, 'update-index', '--cacheinfo', '120000,' + blob + ',docs/dashboard/market.html')
    git(repo, '-c', 'user.name=Disposable', '-c', 'user.email=disposable@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Disposable symlink object')
    with pytest.raises(ValueError, match='PREVIEW_GIT_ASSET_NOT_REGULAR'):
        build_assets(repo, git(repo, 'rev-parse', 'HEAD'))


@pytest.mark.parametrize('commit', ['HEAD', '--help', 'a'*39, 'A'*40, '../main', 'a'*40+';echo bad'])
def test_commit_requires_an_exact_object_id_before_git(tmp_path, commit):
    with pytest.raises(ValueError, match='PREVIEW_COMMIT_INVALID'):
        build_assets(tmp_path, commit)


def test_export_keeps_unavailable_portfolio_and_source_generation_without_fabrication():
    now = datetime(2026, 11, 23, 17, tzinfo=timezone.utc)
    capture = make_market_capture([], deployed_commit='a'*40, generated_at=now.isoformat())
    result = json.loads(project_capture(capture, 'a'*40, now))
    assert len(result['watchlist']) == 20
    assert all(r['status'] == 'UNAVAILABLE' and r['quote'] is None for r in result['watchlist'])
    assert result['generated_at'] == now.isoformat()
    assert result['paper']['positions'] is result['paper']['cash'] is result['paper']['pnl'] is None
    assert result['scanner']['signals'] is None
    assert result['execution_authorized'] is result['independent_verified'] is False


@pytest.mark.parametrize('change', ['authority', 'release', 'malformed'])
def test_private_export_rejects_invalid_evidence(change):
    now = datetime(2026, 11, 23, 17, tzinfo=timezone.utc)
    capture = make_market_capture([], deployed_commit='a'*40, generated_at=now.isoformat())
    value = json.loads(capture)
    if change == 'authority':value['execution_authorized'] = True
    if change == 'release':value['deployed_commit'] = 'b'*40
    if change == 'malformed':value['replies'] = [{}]
    with pytest.raises(ValueError):project_capture(json.dumps(value).encode(), 'a'*40, now)
