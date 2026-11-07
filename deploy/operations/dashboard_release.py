"""Reviewed OS-Python bootstrap; authenticate dependencies before venv execution."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from hope.api.runtime_bootstrap import main

if __name__=="__main__":raise SystemExit(main(release=Path(__file__).resolve().parents[2]))
