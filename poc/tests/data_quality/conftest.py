"""Standalone offline suite: no app, database, torch or model downloads needed."""
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[2]
for entry in (POC / "src", POC / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
