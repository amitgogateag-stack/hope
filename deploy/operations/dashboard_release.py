"""Run only from a reviewed immutable release using the existing HOPE venv."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from hope.api.dashboard_activation import main

if __name__=="__main__":raise SystemExit(main())
