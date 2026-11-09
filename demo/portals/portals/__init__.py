"""HomeBridge demo portals: a pretend customer site and a staff adviser portal. They read and write Fabric live."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "data", _REPO / "action", _REPO / "notebooks"):  # hbdata contracts, the action package, notebook 07 helper
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
