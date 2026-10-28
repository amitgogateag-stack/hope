"""Trusted shared-venv control bootstrap; service runtime is selected separately."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from hope.api.dashboard_activation import main

if __name__=="__main__":raise SystemExit(main())
