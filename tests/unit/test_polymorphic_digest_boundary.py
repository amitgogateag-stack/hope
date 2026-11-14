from pathlib import Path

import pytest

from hope.api.artifact_approval import verify_document
from hope.api.paper_controller import is_hash


class ForgedDigest(str):
    def __eq__(self,other):return True


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
