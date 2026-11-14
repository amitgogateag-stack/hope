from pathlib import Path

import pytest

from hope.api.artifact_approval import verify_document
from hope.api.dashboard_activation import _sha as dashboard_sha
from hope.api.paper_controller import is_hash
from hope.api.runtime_integrity import _sha as runtime_sha,attest_runtime


class ForgedDigest(str):
    def __eq__(self,other):return True


class ForgedFrozenRelease(str):
    def __ne__(self,other):return True


def test_detached_approval_rejects_forged_expected_digest_before_io():
    forged=ForgedDigest('0'*64)
    assert '1'*64==forged
    missing=Path('/definitely/missing/approval.json')
    with pytest.raises(RuntimeError,match='RUNTIME_APPROVAL_INVALID'):
        verify_document(missing,missing.with_suffix('.sig'),missing.with_suffix('.pub'),expected_digest=forged)


def test_paper_authority_hash_pin_rejects_forged_digest_object():
    forged=ForgedDigest('0'*64)
    assert forged=='not the pinned authorization'
    assert is_hash('0'*64) is True
    assert is_hash(forged) is False


def test_runtime_manifest_rejects_forged_digest_before_io():
    forged=ForgedDigest('0'*64)
    assert '1'*64==forged
    with pytest.raises(RuntimeError,match='RUNTIME_MANIFEST_CHANGED'):
        attest_runtime(Path('/definitely/missing/runtime-root'),'2'*40,'shared',expected_manifest=forged)


def test_activation_boundaries_reject_forged_frozen_release():
    frozen='7e9d341742818a97accce3e2301b1c7f96596795'
    forged=ForgedFrozenRelease(frozen)
    assert str(forged)==frozen and forged!=frozen
    with pytest.raises(RuntimeError,match='RUNTIME_RELEASE_INVALID'):
        runtime_sha(forged)
    with pytest.raises(RuntimeError,match='DASHBOARD_SHA_INVALID'):
        dashboard_sha(forged)
