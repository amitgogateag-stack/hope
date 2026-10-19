"""Adversarial unit tests for the exporter installation inspection.

No AWS state or systemd daemon is touched.
"""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import subprocess

MODULE = Path(__file__).resolve().parents[1] / "deploy" / "operations" / "inspect_exporter_install.py"
spec = importlib.util.spec_from_file_location("exporter_install_inspection", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_missing_unit_metadata_is_accepted_only_as_metadata():
    output = ("LoadState=not-found\nActiveState=inactive\nUnitFileState=\n"
              "FragmentPath=\nDropInPaths=\n").encode()
    with patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, b"")):
        result = mod.unit_metadata("hope-ops-dashboard-export.service")
    assert result["LoadState"] == "not-found"
    assert result["FragmentPath"] == ""


def test_duplicate_unit_property_fails_closed():
    output = b"LoadState=not-found\nLoadState=loaded\n"
    with patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, b"")):
        try:
            mod.unit_metadata("hope-ops-dashboard-export.service")
        except ValueError as exc:
            assert str(exc) == "UNIT_METADATA_DUPLICATE"
        else:
            raise AssertionError("duplicate property accepted")


def test_incomplete_unit_metadata_fails_closed():
    output = b"LoadState=not-found\nActiveState=inactive\n"
    with patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, b"")):
        try:
            mod.unit_metadata("hope-ops-dashboard-export.service")
        except ValueError as exc:
            assert str(exc) == "UNIT_METADATA_INCOMPLETE"
        else:
            raise AssertionError("incomplete property accepted")


def test_oversized_unit_output_fails_closed():
    with patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"x" * (mod.MAX_BYTES + 1), b"")):
        try:
            mod.unit_metadata("hope-ops-dashboard-export.timer")
        except ValueError as exc:
            assert str(exc) == "UNIT_METADATA_UNVERIFIED"
        else:
            raise AssertionError("oversized output accepted")


def test_failed_systemctl_fails_closed():
    with patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"private")):
        try:
            mod.unit_metadata("hope-ops-dashboard-export.timer")
        except ValueError as exc:
            assert str(exc) == "UNIT_METADATA_UNVERIFIED"
        else:
            raise AssertionError("failed systemctl accepted")
