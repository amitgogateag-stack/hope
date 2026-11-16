#!/usr/bin/env python3
"""Operator-invoked read-only PAPER preflight; no installation or activation."""
from hope.api.paper_preflight import main

if __name__ == '__main__':
    raise SystemExit(main())
