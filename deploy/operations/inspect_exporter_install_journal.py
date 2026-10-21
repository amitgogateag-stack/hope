#!/usr/bin/env python3
"""Inspect installation evidence without changing files or authorizing recovery."""
import argparse
import importlib.util
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-shared", required=True)
    args = parser.parse_args(argv)
    try:
        path = Path(__file__).with_name("exporter_install_journal.py")
        spec = importlib.util.spec_from_file_location("exporter_journal_inspection", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        record = module.inspect_journal(Path("/opt/hope-operations"))
        if record["sha"] != args.expected_shared:
            raise ValueError("release mismatch")
        print(json.dumps(dict(status="REVIEW_REQUIRED", phase=record["phase"],
            sequence=record["sequence"], unit_count=len(record["units"]),
            host_changes=False, recovery_authorized=False, live_enabled=False)))
    except Exception:
        print("EXPORTER_JOURNAL_INSPECTION_BLOCKED")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
