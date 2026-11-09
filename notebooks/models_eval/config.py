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
LIFT_K = 10  # lift is reported for the 10 highest scores per cohort. A metric only. It is not a review capacity.
MAX_SCORE_AGE_MINUTES = 1440  # placeholder: the team has not confirmed it. The report and the action policy use the same value.
THRESHOLD_RULES = ("max_f1", "min_cost")
CLASSIFIER_NAME = "hb-refi-clf"
CLUSTER_NAME = "hb-segments"
EXPERIMENT_NAME = "homebridge-refinancing-risk"
RUN_KINDS = ("scheduled", "checkpoint", "manual")


@dataclass(frozen=True)
class ThresholdRule:
    """How training picks the alert threshold. A setting, not a number. See thresholds.py."""
    name: str = "max_f1"
    cost_missed_leaver: float | None = None
    cost_review: float | None = None

    def params(self) -> dict:
        return {"cost_missed_leaver": self.cost_missed_leaver, "cost_review": self.cost_review} if self.name == "min_cost" else {}


def threshold_rule(name: str | None = None, cost_missed_leaver: float | None = None, cost_review: float | None = None) -> ThresholdRule:
    """The rule from the arguments, then the HB_THRESHOLD_RULE, HB_COST_MISSED_LEAVER and HB_COST_REVIEW variables, then max_f1."""
    name = name or os.environ.get("HB_THRESHOLD_RULE") or "max_f1"
    if name not in THRESHOLD_RULES:
        raise ValueError(f"Unknown threshold rule {name!r}. Known: {THRESHOLD_RULES}.")

    def num(value, env):
        raw = value if value is not None else os.environ.get(env)
        return None if raw in (None, "") else float(raw)

    return ThresholdRule(name, num(cost_missed_leaver, "HB_COST_MISSED_LEAVER"), num(cost_review, "HB_COST_REVIEW"))


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
