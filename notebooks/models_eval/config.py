"""Paths and fixed settings for the Models and evaluation work package.

Everything here runs locally on Parquet folders under data/output/. Nothing has
run in Fabric yet. In Fabric, set HB_OUTPUT_ROOT to the Lakehouse Files path and
HB_MLFLOW_URI to "fabric" so that MLflow uses the workspace tracking server.
"""
from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

PKG_DIR = Path(__file__).resolve().parent
NOTEBOOKS_DIR = PKG_DIR.parent
REPO_DIR = NOTEBOOKS_DIR.parent
DATA_DIR = REPO_DIR / "data"


def ensure_hbdata() -> None:
    """Make the Data and replay package (data/hbdata) importable."""
    if str(DATA_DIR) not in sys.path:
        sys.path.insert(0, str(DATA_DIR))


TARGET = "confirmed_external_refinancing"
REVIEWS_PER_RUN = 10
CLASSIFIER_NAME = "hb-refi-clf"
CLUSTER_NAME = "hb-segments"
EXPERIMENT_NAME = "homebridge-refinancing-risk"
RUN_KINDS = ("scheduled", "checkpoint", "manual")


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def lakehouse(self) -> Path:
        return self.root / "lakehouse"

    @property
    def features(self) -> Path:
        return self.lakehouse / "features"

    @property
    def clean(self) -> Path:
        return self.lakehouse / "clean"

    @property
    def gold(self) -> Path:
        return self.lakehouse / "gold"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def eventstream(self) -> Path:
        return self.root / "eventstream"

    @property
    def mlflow_uri(self) -> str | None:
        uri = os.environ.get("HB_MLFLOW_URI")
        if uri == "fabric":
            return None  # Fabric notebooks use the workspace tracking server.
        return uri or "sqlite:///" + (self.root / "mlflow" / "mlflow.db").as_posix()


def paths(output_root: str | Path | None = None) -> Paths:
    root = output_root or os.environ.get("HB_OUTPUT_ROOT") or DATA_DIR / "output"
    return Paths(Path(root))


@dataclass(frozen=True)
class SplitConfig:
    """Later-cohort split. Cohort = deal-end month (cohort_id).

    Training and validation labels must be observed before the simulated model
    release. Test cohorts end later. Their borrowers stay out of training.
    """
    train_cohorts: tuple[str, ...] = ("2026-01", "2026-02", "2026-03")
    validation_cohorts: tuple[str, ...] = ("2026-04",)
    test_cohorts: tuple[str, ...] = ("2026-05", "2026-06")
    release_at: str = "2026-06-01T00:00:00Z"
    seed: int = 20261026

    @property
    def release_ts(self) -> pd.Timestamp:
        return pd.Timestamp(self.release_at)

    def to_json(self) -> dict:
        return asdict(self)
