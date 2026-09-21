"""Makes the sibling scripts (make_run.py, route_arm_a.py, account.py) importable
by their bare module names, regardless of the cwd pytest is invoked from."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
