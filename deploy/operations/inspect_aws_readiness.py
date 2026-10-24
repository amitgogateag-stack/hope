#!/usr/bin/env python3
"""Run the reviewed, read-only AWS reconciliation from an operations release."""

from hope.api.aws_readiness import main


if __name__ == "__main__":
    raise SystemExit(main())
