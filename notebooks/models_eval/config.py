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
THRESHOLD_RULES = ("min_precision", "max_f1", "min_cost")
DEFAULT_THRESHOLD_RULE = "min_precision"
DEFAULT_MIN_PRECISION = 0.6  # at least 60% of alerts should be real leavers on validation data. The team has not confirmed it.
CLASSIFIER_NAME = "hb-refi-clf"
CLUSTER_NAME = "hb-segments"
EXPERIMENT_NAME = "homebridge-refinancing-risk"
RUN_KINDS = ("scheduled", "checkpoint", "manual")


@dataclass(frozen=True)
class ThresholdRule:
    """How training picks the alert threshold. A setting, not a number. See thresholds.py."""
    name: str = DEFAULT_THRESHOLD_RULE
    cost_missed_leaver: float | None = None
    cost_review: float | None = None
    min_precision: float | None = None

    def params(self) -> dict:
        if self.name == "min_cost":
            return {"cost_missed_leaver": self.cost_missed_leaver, "cost_review": self.cost_review}
        return {"min_precision": self.min_precision} if self.name == "min_precision" else {}


def threshold_rule(name: str | None = None, cost_missed_leaver: float | None = None, cost_review: float | None = None,
                   min_precision: float | None = None) -> ThresholdRule:
    """The rule from the arguments, then the HB_THRESHOLD_RULE, HB_COST_MISSED_LEAVER, HB_COST_REVIEW and HB_MIN_PRECISION
    variables, then the default rule (min_precision, target 0.6)."""
    name = name or os.environ.get("HB_THRESHOLD_RULE") or DEFAULT_THRESHOLD_RULE
    if name not in THRESHOLD_RULES:
        raise ValueError(f"Unknown threshold rule {name!r}. Known: {THRESHOLD_RULES}.")

    def num(value, env):
        raw = value if value is not None else os.environ.get(env)
        return None if raw in (None, "") else float(raw)

    target = num(min_precision, "HB_MIN_PRECISION")
    if name == "min_precision":
        target = DEFAULT_MIN_PRECISION if target is None else target
        if not 0 < target < 1:
            raise ValueError("min_precision must be above 0 and below 1.")
    return ThresholdRule(name, num(cost_missed_leaver, "HB_COST_MISSED_LEAVER"), num(cost_review, "HB_COST_REVIEW"),
                         target if name == "min_precision" else None)


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
