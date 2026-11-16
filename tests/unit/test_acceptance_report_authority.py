"""Report producers cannot turn local completion or labels into authority."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from hope.api import release_acceptance as acceptance
from hope.api import deployment_rehearsal as rehearsal
from hope.api import acceptance_evidence, rehearsal_evidence
from test_m3_dashboard_readiness import verified_runtime


class _EqualToEverything:
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False


class _CompatibleSpoof(str):
    def __hash__(self):
        return hash('COMPATIBLE')

    def __eq__(self, other):
        return other == 'COMPATIBLE'


class _ObservationMap(dict):
    pass


class _ReleaseIdentity(str):
    pass


def test_perfect_caller_labels_do_not_supply_independent_acceptance():
    now = datetime.now(timezone.utc)
    labels = dict(release_commit='a'*40, generated_at=now.isoformat(),
        process_identity='d'*64, database='COMPATIBLE', scheduler='CURRENT',
        market_data='VERIFIED', reconciliation='CLEAR', risk='CLEAR', live_enabled=False)
    result = acceptance.acceptance_observation(verified_runtime(), labels,
        expected_sha='a'*40, now=now)
    assert result['state'] == 'BLOCKED'
    assert 'REVIEW_INDEPENDENT_ACCEPTANCE_AUTHORITY' in result['blockers']
    assert result['execution_authorized'] is False


def test_polymorphic_release_identity_cannot_suppress_mismatch_blocker():
    now = datetime.now(timezone.utc)
    labels = dict(release_commit=_EqualToEverything(), generated_at=now.isoformat(),
        process_identity='d'*64, database='COMPATIBLE', scheduler='CURRENT',
        market_data='VERIFIED', reconciliation='CLEAR', risk='CLEAR', live_enabled=False)
    result = acceptance.acceptance_observation(verified_runtime(), labels,
        expected_sha='a'*40, now=now)
    assert 'REVIEW_DEPLOYMENT_IDENTITY' in result['blockers']


def test_polymorphic_status_label_cannot_impersonate_canonical_value():
    now = datetime.now(timezone.utc)
    labels = dict(release_commit='a'*40, generated_at=now.isoformat(),
        process_identity='d'*64, database=_CompatibleSpoof('UNVERIFIED'), scheduler='CURRENT',
        market_data='VERIFIED', reconciliation='CLEAR', risk='CLEAR', live_enabled=False)
    result = acceptance.acceptance_observation(verified_runtime(), labels,
        expected_sha='a'*40, now=now)
    assert 'REVIEW_DATABASE' in result['blockers']


def test_polymorphic_observation_container_is_not_canonical_evidence():
    now = datetime.now(timezone.utc)
    labels = _ObservationMap(release_commit='a'*40, generated_at=now.isoformat(),
        process_identity='d'*64, database='COMPATIBLE', scheduler='CURRENT',
        market_data='VERIFIED', reconciliation='CLEAR', risk='CLEAR', live_enabled=False)
    result = acceptance.acceptance_observation(verified_runtime(), labels,
        expected_sha='a'*40, now=now)
    assert 'REVIEW_MISSING_EVIDENCE' in result['blockers']


def test_polymorphic_expected_release_identity_is_rejected():
    with pytest.raises(RuntimeError, match='RUNTIME_RELEASE_INVALID'):
        acceptance.acceptance_observation(verified_runtime(), {},
            expected_sha=_ReleaseIdentity('a'*40))


def test_successful_local_rehearsal_cannot_issue_verified_acceptance(monkeypatch, tmp_path):
    # Exercise the report producer after modeled local completion, without
    # activation, signing or an invented independent acceptance authority.
    activate = Mock()
    save = Mock()
    monkeypatch.setattr(rehearsal.activation, 'activate', activate)
    monkeypatch.setattr(rehearsal.activation, '_save', save)
    host = SimpleNamespace(inspect=Mock(return_value=verified_runtime()))
    layout = SimpleNamespace(root=tmp_path)
    result = rehearsal.rehearse_upgrade(layout, host, target='a'*40,
        shared='b'*40, ci_run=123, ci_attempt=1)
    activate.assert_called_once()
    assert result['state'] == 'UNVERIFIED'
    assert result['lab_result'] == 'COMPLETED'
    assert result['independent_acceptance'] == 'UNVERIFIED'
    assert result['execution_authorized'] is False and result['host_changes'] is False
    save.assert_called_once_with(tmp_path/'rehearsal-result.json', result)


def test_authenticated_bytes_preserve_missing_acceptance_authority(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    result = acceptance.acceptance_observation(verified_runtime(), {}, expected_sha='a'*40, now=now)
    payload = json.dumps(result).encode()
    expected = sha256(payload).hexdigest()
    # Model only the native file-read boundary. Matching bytes cannot remove
    # the producer's missing independent-authority blocker.
    read = Mock(return_value=payload)
    monkeypatch.setattr(acceptance_evidence, 'authenticated_bounded_regular_read', read)
    projected = acceptance_evidence.read_acceptance(tmp_path/'report', expected_sha256=expected)
    read.assert_called_once_with(tmp_path/'report', 8192, expected_sha256=expected)
    assert projected['state'] == 'BLOCKED'
    assert 'REVIEW_INDEPENDENT_ACCEPTANCE_AUTHORITY' in projected['blockers']
    assert 'REVIEW_MISSING_EVIDENCE' in projected['blockers']
    assert projected['execution_authorized'] is False


def test_rehearsal_completion_remains_unverified_even_with_matching_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(rehearsal.activation, 'activate', Mock())
    monkeypatch.setattr(rehearsal.activation, '_save', Mock())
    result = rehearsal.rehearse_upgrade(SimpleNamespace(root=tmp_path),
        SimpleNamespace(inspect=Mock(return_value=verified_runtime())),
        target='a'*40, shared='b'*40, ci_run=123, ci_attempt=1)
    payload = json.dumps(result).encode()
    read = Mock(return_value=payload)
    monkeypatch.setattr(rehearsal_evidence, 'authenticated_bounded_regular_read', read)
    projected = rehearsal_evidence.read_rehearsal(tmp_path/'report', expected_sha256=sha256(payload).hexdigest())
    read.assert_called_once()
    assert projected['state'] == 'UNVERIFIED'
    assert projected['lab_result'] == 'COMPLETED'
    assert projected['independent_acceptance'] == 'UNVERIFIED'
    assert projected['execution_authorized'] is False
