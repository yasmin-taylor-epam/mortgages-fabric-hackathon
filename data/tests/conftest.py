import sys
from pathlib import Path

import pandas as pd
import pytest

DATA_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DATA_DIR))

from hbdata import generator, snapshots, validation  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return generator.GenConfig()


@pytest.fixture(scope="session")
def generated(cfg):
    return generator.generate(cfg)


@pytest.fixture(scope="session")
def frames(generated):
    return generated[0]


@pytest.fixture(scope="session")
def manifest(generated):
    return generated[1]


@pytest.fixture(scope="session")
def validated(frames, cfg):
    return validation.validate(frames, cfg.as_of)


@pytest.fixture(scope="session")
def clean(validated):
    return validated["clean"]


@pytest.fixture(scope="session")
def checkpoint_snapshots(clean, cfg):
    req = snapshots.checkpoint_requests(clean["mortgage_account"], cfg.as_of)
    return snapshots.build_snapshots(clean, req, cfg.as_of)


def ts(s):
    return pd.Timestamp(s)
