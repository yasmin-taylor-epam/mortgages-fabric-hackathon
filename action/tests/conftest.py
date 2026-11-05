import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

from action_helpers import Harness  # noqa: E402


@pytest.fixture
def harness():
    h = Harness()
    yield h
    h.store.close()
