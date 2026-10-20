"""Adversarial unit tests for the exporter installation inspection.

No AWS state or systemd daemon is touched.
"""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import subprocess
import os
import json
import shutil
import pytest

MODULE = Path(__file__).resolve().parents[1] / "deploy" / "operations" / "inspect_exporter_install.py"
spec = importlib.util.spec_from_file_location("exporter_install_inspection", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_missing_unit_metadata_is_accepted_only_as_metadata():
    output = ("LoadState=not-found\nActiveState=inactive\nUnitFileState=\n"
              "FragmentPath=\nDropInPaths=\n").encode()
    with patch.object(mod, "_bounded_capture", return_value=subprocess.CompletedProcess([], 0, output, b"")):
        result = mod.unit_metadata("hope-ops-dashboard-export.service")
    assert result["LoadState"] == "not-found"
    assert result["FragmentPath"] == ""


def test_duplicate_unit_property_fails_closed():
    output = b"LoadState=not-found\nLoadState=loaded\n"
    with patch.object(mod, "_bounded_capture", return_value=subprocess.CompletedProcess([], 0, output, b"")):
        try:
            mod.unit_metadata("hope-ops-dashboard-export.service")
        except ValueError as exc:
            assert str(exc) == "UNIT_METADATA_DUPLICATE"
        else:
            raise AssertionError("duplicate property accepted")


def test_incomplete_unit_metadata_fails_closed():
    output = b"LoadState=not-found\nActiveState=inactive\n"
    with patch.object(mod, "_bounded_capture", return_value=subprocess.CompletedProcess([], 0, output, b"")):
        try:
            mod.unit_metadata("hope-ops-dashboard-export.service")
        except ValueError as exc:
            assert str(exc) == "UNIT_METADATA_INCOMPLETE"
        else:
            raise AssertionError("incomplete property accepted")


def test_oversized_unit_output_fails_closed():
    with patch.object(mod, "_bounded_capture", return_value=subprocess.CompletedProcess([], 0, b"x" * (mod.MAX_BYTES + 1), b"")):
        try:
            mod.unit_metadata("hope-ops-dashboard-export.timer")
        except ValueError as exc:
            assert str(exc) == "UNIT_METADATA_UNVERIFIED"
        else:
            raise AssertionError("oversized output accepted")


def test_failed_systemctl_fails_closed():
    with patch.object(mod, "_bounded_capture", return_value=subprocess.CompletedProcess([], 1, b"", b"")):
        try:
            mod.unit_metadata("hope-ops-dashboard-export.timer")
        except ValueError as exc:
            assert str(exc) == "UNIT_METADATA_UNVERIFIED"
        else:
            raise AssertionError("failed systemctl accepted")


def release_layout(tmp_path):
    sha = "1" * 40
    root = tmp_path / "root"
    release = root / "releases" / sha
    operations = release / "deploy" / "operations"
    operations.mkdir(parents=True)
    root.chmod(0o750)
    (root / "releases").chmod(0o750)
    release.chmod(0o750)
    (release / "deploy").chmod(0o750)
    operations.chmod(0o750)
    for name in mod.NAMES:
        source = MODULE.parent / name
        shutil.copyfile(source, operations / name)
        (operations / name).chmod(0o600)
    (root / "deployed.env").write_text(f"HOPE_DEPLOYED_COMMIT={sha}\n")
    (root / "deployed.env").chmod(0o600)
    (root / "current").symlink_to(release, target_is_directory=True)
    return root, release, sha


def verify(tmp_path):
    root, release, sha = release_layout(tmp_path)
    with patch.object(mod, "ROOT", root):
        mod.verify_release(sha, owner_uid=os.getuid())
    return root, release, sha


def test_release_identity_accepts_exact_safe_layout(tmp_path):
    verify(tmp_path)


def test_untrusted_current_target_outside_release_store_is_rejected(tmp_path):
    root, release, sha = release_layout(tmp_path)
    attacker = tmp_path / "attacker"
    shutil.copytree(release, attacker)
    (root / "current").unlink()
    (root / "current").symlink_to(attacker, target_is_directory=True)
    with patch.object(mod, "ROOT", root), pytest.raises(ValueError, match="SHARED_POINTER_MISMATCH"):
        mod.verify_release(sha, owner_uid=os.getuid())


@pytest.mark.parametrize("value", ["1" * 39, mod.FROZEN_BASELINE])
def test_invalid_or_frozen_expected_shared_is_rejected(tmp_path, value):
    root, _, _ = release_layout(tmp_path)
    with patch.object(mod, "ROOT", root), pytest.raises(ValueError, match="EXPECTED_SHARED_INVALID"):
        mod.verify_release(value, owner_uid=os.getuid())


def test_deployed_identity_mismatch_is_rejected(tmp_path):
    root, _, sha = release_layout(tmp_path)
    (root / "deployed.env").write_text("HOPE_DEPLOYED_COMMIT=" + "2" * 40 + "\n")
    with patch.object(mod, "ROOT", root), pytest.raises(ValueError, match="DEPLOYED_IDENTITY_MISMATCH"):
        mod.verify_release(sha, owner_uid=os.getuid())


@pytest.mark.parametrize("mutation", ["content", "permissions", "hardlink"])
def test_unsafe_or_changed_template_is_rejected(tmp_path, mutation):
    root, release, sha = release_layout(tmp_path)
    template = release / "deploy" / "operations" / mod.NAMES[0]
    if mutation == "content":
        template.write_text("[Service]\nExecStart=/tmp/attacker\n")
        expected = "TEMPLATE_IDENTITY_MISMATCH"
    elif mutation == "permissions":
        template.chmod(0o620)
        expected = "RELEASE_FILE_UNSAFE"
    else:
        os.link(template, tmp_path / "second-link")
        expected = "RELEASE_FILE_UNSAFE"
    with patch.object(mod, "ROOT", root), pytest.raises(ValueError, match=expected):
        mod.verify_release(sha, owner_uid=os.getuid())


def test_group_writable_release_subdirectory_is_rejected(tmp_path):
    root, release, sha = release_layout(tmp_path)
    (release / "deploy" / "operations").chmod(0o770)
    with patch.object(mod, "ROOT", root), pytest.raises(ValueError, match="RELEASE_DIRECTORY_UNSAFE"):
        mod.verify_release(sha, owner_uid=os.getuid())


@pytest.mark.parametrize("mutation", ["symlink", "fifo", "hardlink", "permissions"])
def test_unsafe_config_metadata_is_rejected(tmp_path, mutation):
    config = tmp_path / "config"
    target = tmp_path / "target"
    target.write_text("secret\n")
    target.chmod(0o600)
    if mutation == "symlink":
        config.symlink_to(target)
    elif mutation == "fifo":
        os.mkfifo(config)
    elif mutation == "hardlink":
        os.link(target, config)
    else:
        config.write_text("secret\n")
        config.chmod(0o640)
    with pytest.raises(ValueError, match="CONFIG_UNSAFE"):
        mod._verify_private_file(config, owner_uid=os.getuid())


def test_expected_shared_argument_and_root_are_mandatory(capsys):
    with patch.object(mod.os, "geteuid", return_value=1000):
        assert mod.main([]) == 2
        result = json.loads(capsys.readouterr().out)
        assert result["result"] == "BLOCKED"
        assert result["release_identity"] == "NOT_VERIFIED"
