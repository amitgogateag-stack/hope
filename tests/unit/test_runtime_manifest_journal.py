"""Manifest-bound activation, legacy recovery, and durable failure fencing."""
import json
from unittest.mock import Mock

import pytest

from hope.api import dashboard_activation as m
from test_dashboard_only_activation import OLD,NEW,THIRD,setup
from test_activation_guard import guard_platform


def switch(layout,host,target=NEW,mode="shared"):
    host.runtime_manifests={OLD:"a"*64,NEW:"b"*64,THIRD:"b"*64}
    return m.activate(layout,host,target=target,shared=OLD,ci_run=123,ci_attempt=1,runtime=mode)


@pytest.mark.parametrize("mode",["shared","release"])
def test_v3_records_both_approved_runtimes_and_nested_rollback_restores_exact_prior(setup,mode):
    layout,host=setup;switch(layout,host,mode=mode)
    first=layout.journal.read_bytes();record=m._record(layout)
    assert record["schema"]=="hope.dashboard-activation.v3"
    assert record["runtime_manifest"]=="b"*64 and record["previous_runtime_manifest"]=="a"*64
    assert m.runtime_gate(layout,NEW)["runtime_integrity"]["state"]=="VERIFIED"
    switch(layout,host,THIRD,"release")
    m.rollback(layout,host,target=THIRD,shared=OLD)
    assert layout.journal.read_bytes()==first
    m.rollback(layout,host,target=NEW,shared=OLD)
    assert m._record(layout) is None and m._pointer(layout,"current")==OLD


@pytest.mark.parametrize("field,value",[("runtime_manifest",None),("runtime_manifest","bad"),
    ("previous_runtime_manifest",True),("runtime","auto"),("schema","hope.dashboard-activation.v1")])
def test_missing_or_corrupt_v3_runtime_binding_blocks_requests_and_recovery(setup,field,value):
    layout,host=setup;switch(layout,host)
    record=m._record(layout);record[field]=value;m._save(layout.journal,record)
    host.calls.clear()
    with pytest.raises((RuntimeError,TypeError)):m.runtime_gate(layout,NEW)
    with pytest.raises((RuntimeError,TypeError)):m.rollback(layout,host,target=NEW,shared=OLD)
    assert host.calls==[]


def test_gate_forwards_the_durable_manifest_digest_and_closes_on_mismatch(setup,monkeypatch):
    layout,host=setup;switch(layout,host,mode="release")
    check=Mock(side_effect=RuntimeError("RUNTIME_MANIFEST_CHANGED"));monkeypatch.setattr(m,"attest_runtime",check)
    with pytest.raises(RuntimeError,match="RUNTIME_MANIFEST_CHANGED"):m.runtime_gate(layout,NEW)
    check.assert_called_once_with(layout.root,NEW,"release",expected_manifest="b"*64,
        release_source="docs/dashboard/preview.html")


@pytest.mark.parametrize("legacy",["hope.dashboard-activation.v1","hope.dashboard-activation.v2"])
def test_legacy_journals_remain_observable_without_claiming_runtime_verification(setup,legacy):
    layout,host=setup;switch(layout,host)
    record=m._record(layout);record.pop("runtime_manifest");record.pop("previous_runtime_manifest");record["schema"]=legacy
    if legacy.endswith("v1"):record.pop("runtime");record.pop("previous_runtime")
    m._save(layout.journal,record)
    evidence=m.runtime_gate(layout,NEW)["runtime_integrity"]
    assert evidence["state"]=="UNVERIFIED" and evidence["execution_authorized"] is False
    prior=layout.journal.read_bytes();switch(layout,host,THIRD,"release")
    def start(timer):
        assert m._record(layout)["schema"]==legacy
        assert m._record(layout)["phase"]=="ROLLING_BACK"
    host.start=start;m.rollback(layout,host,target=THIRD,shared=OLD)
    assert layout.journal.read_bytes()==prior


def test_interrupted_manifest_activation_keeps_recovery_fence_and_never_switches_paper(setup,monkeypatch):
    layout,host=setup;switch(layout,host)
    record=m._record(layout);record["phase"]="VERIFYING";m._save(layout.journal,record)
    monkeypatch.setattr(m,"_owner_alive",lambda value:False)
    with pytest.raises(RuntimeError,match="RECOVERY_REQUIRED"):m.runtime_gate(layout,NEW)
    assert m._pointer(layout,"current")==OLD
    m.rollback(layout,host,target=NEW,shared=OLD)
    assert not layout.journal.exists()


def test_prior_manifest_substitution_in_backup_is_rejected_before_host_mutation(setup):
    layout,host=setup;switch(layout,host);switch(layout,host,THIRD)
    record=m._record(layout);backup=layout.root/("dashboard-backup-"+record["backup_id"]+".json")
    value=json.loads(backup.read_text());value["previous_record"]["runtime_manifest"]="c"*64;m._save(backup,value,0o600)
    host.calls.clear()
    with pytest.raises(RuntimeError,match="BACKUP_INVALID"):m.rollback(layout,host,target=THIRD,shared=OLD)
    assert host.calls==["paper_safe"]


def test_duplicate_journal_identity_never_uses_last_value_as_authority(setup):
    layout,host=setup;switch(layout,host)
    original=layout.journal.read_text()
    layout.journal.write_text(original[:-1]+',"runtime_manifest":"'+'c'*64+'"}')
    with pytest.raises(ValueError,match="DUPLICATE_KEY"):m.runtime_gate(layout,NEW)


def test_interrupted_legacy_rollback_retains_manifest_binding_in_private_backup(setup,monkeypatch):
    layout,host=setup
    m.activate(layout,host,target=NEW,shared=OLD,ci_run=123,ci_attempt=1)  # existing v1 deployment
    switch(layout,host,THIRD,"release")
    host.start=Mock(side_effect=RuntimeError("interrupted restart"))
    with pytest.raises(RuntimeError):m.rollback(layout,host,target=THIRD,shared=OLD)
    record=m._record(layout)
    assert record["schema"]=="hope.dashboard-activation.v1" and record["phase"]=="ROLLBACK_FAILED"
    assert m._backup(layout,record)["runtime_binding"]["previous_runtime_manifest"]=="b"*64
    verifier=m.Host(layout);verifier.paper_safe=Mock();verifier.release=Mock(return_value=layout.root/"releases"/NEW)
    verifier.database=Mock();verifier.run=Mock()
    attest=Mock(side_effect=RuntimeError("RUNTIME_MANIFEST_CHANGED"));monkeypatch.setattr(m,"attest_runtime",attest)
    with pytest.raises(RuntimeError,match="RUNTIME_MANIFEST_CHANGED"):verifier.rollback_preflight(NEW)
    assert attest.call_args.kwargs["expected_manifest"]=="b"*64
    verifier.run.assert_not_called()
