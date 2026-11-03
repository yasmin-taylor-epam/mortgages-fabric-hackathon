"""Input contract checks, labels and the later-cohort split.

- INV-2: features use only data known at the checkpoint. Every source watermark
  in a snapshot must be at or before its cutoff_at.
- INV-4: an unknown or incomplete refinancing outcome gets no model label. It
  never counts as retained (label 0).
- Training labels must exist before the simulated model release.
- Test cohorts end later. Their borrowers stay out of training.

The Data and replay package owns the snapshot and outcome schemas
(data/contracts/feature_snapshot.schema.json and observed_outcome.schema.json).
"""
from __future__ import annotations

import json

import pandas as pd

from . import features
from .config import SplitConfig


class InvariantError(ValueError):
    """An input breaks a blueprint invariant. The run must stop."""


SNAPSHOT_KEYS = ["snapshot_id", "mortgage_id", "cycle_id", "cohort_id", "cutoff_at", "snapshot_kind",
                 "feature_version", "source_watermark", "evidence_ids"]
OUTCOME_COLUMNS = ["mortgage_id", "cycle_id", "outcome_type", "outcome_observed_at", "label_status", "label"]
POSITIVE = {"external_refinance"}
NEGATIVE = {"product_transfer", "continued_servicing"}


def require_columns(df: pd.DataFrame, cols: list[str], name: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(f"{name} lacks required columns: {missing}")


def inv2_violations(snap: pd.DataFrame) -> list[str]:
    """Snapshot IDs whose source watermark is after the cutoff, or is unreadable."""
    require_columns(snap, ["snapshot_id", "cutoff_at", "source_watermark"], "feature_snapshot")
    bad = []
    cutoffs = pd.to_datetime(snap["cutoff_at"], utc=True)
    for sid, cutoff, wm in zip(snap["snapshot_id"], cutoffs, snap["source_watermark"]):
        try:
            marks = json.loads(wm) if isinstance(wm, str) else dict(wm)
        except (TypeError, ValueError):
            bad.append(sid)
            continue
        for value in marks.values():
            if value is not None and pd.Timestamp(value).tz_convert("UTC") > cutoff:
                bad.append(sid)
                break
    return bad


def check_snapshots(snap: pd.DataFrame, run_cutoff=None, feature_version: str | None = None) -> None:
    """Raise InvariantError if any snapshot breaks INV-2 or does not match the model."""
    require_columns(snap, SNAPSHOT_KEYS + features.SOURCE_COLUMNS, "feature_snapshot")
    bad = inv2_violations(snap)
    if bad:
        raise InvariantError(f"INV-2: {len(bad)} snapshots hold data after their cutoff, for example {bad[:3]}.")
    if run_cutoff is not None:
        late = snap[pd.to_datetime(snap["cutoff_at"], utc=True) > pd.Timestamp(run_cutoff)]
        if len(late):
            raise InvariantError(f"INV-2: {len(late)} snapshots have a cutoff after the run cutoff {run_cutoff}.")
    if feature_version is not None:
        other = set(snap["feature_version"]) - {feature_version}
        if other:
            raise InvariantError(f"Snapshots use feature_version {sorted(other)}, but the model expects {feature_version}.")
    if snap["snapshot_id"].duplicated().any():
        raise InvariantError("Duplicate snapshot_id values in one scoring input.")


def labels(outcomes: pd.DataFrame, release_at=None) -> pd.DataFrame:
    """Model labels. Only labelled outcomes count (INV-4).

    With release_at, only labels observed before the release are returned.
    """
    require_columns(outcomes, OUTCOME_COLUMNS, "observed_outcome")
    lab = outcomes[outcomes["label_status"] == "labelled"].copy()
    wrong_type = lab[~lab["outcome_type"].isin(POSITIVE | NEGATIVE)]
    if len(wrong_type):
        raise InvariantError(f"INV-4: {len(wrong_type)} labelled rows have outcome types that cannot carry a label.")
    expected = lab["outcome_type"].isin(POSITIVE).astype(int)
    if lab["label"].isna().any() or (lab["label"].astype(int) != expected).any():
        raise InvariantError("INV-4: label does not match outcome_type for some labelled rows.")
    lab["label"] = expected
    if release_at is not None:
        observed = pd.to_datetime(lab["outcome_observed_at"], utc=True)
        lab = lab[observed.notna() & (observed < pd.Timestamp(release_at))]
    return lab[["mortgage_id", "cycle_id", "outcome_type", "outcome_observed_at", "label"]].reset_index(drop=True)


def labelled_checkpoints(snap: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    """Checkpoint snapshots joined to their labels. Excluded and pending outcomes drop out."""
    cp = snap[snap["snapshot_kind"] == "checkpoint"]
    return cp.merge(labels(outcomes), on=["mortgage_id", "cycle_id"], how="inner", validate="one_to_one")


def split(frame: pd.DataFrame, account_party: pd.DataFrame | None, cfg: SplitConfig) -> tuple[dict, dict]:
    """Return train, validation and test frames plus a summary.

    Train and validation keep only labels observed before cfg.release_at.
    Rows that share a mortgage or an approved borrower with the test cohorts
    leave train and validation.
    """
    by = {name: frame[frame["cohort_id"].isin(cohorts)].copy()
          for name, cohorts in (("train", cfg.train_cohorts), ("validation", cfg.validation_cohorts), ("test", cfg.test_cohorts))}
    summary = {"release_at": cfg.release_at, "dropped_after_release": {}, "dropped_shared_borrower": {}}
    for name in ("train", "validation"):
        obs = pd.to_datetime(by[name]["outcome_observed_at"], utc=True)
        keep = obs < cfg.release_ts
        summary["dropped_after_release"][name] = int((~keep).sum())
        by[name] = by[name][keep]

    test_mortgages = set(by["test"]["mortgage_id"])
    test_parties: set = set()
    parties = None
    if account_party is not None and len(account_party):
        parties = account_party[account_party["mapping_status"] == "approved"][["mortgage_id", "party_id"]].drop_duplicates()
        test_parties = set(parties[parties["mortgage_id"].isin(test_mortgages)]["party_id"])
    for name in ("train", "validation"):
        df = by[name]
        shared = df["mortgage_id"].isin(test_mortgages)
        if parties is not None and test_parties:
            linked = set(parties[parties["party_id"].isin(test_parties)]["mortgage_id"])
            shared |= df["mortgage_id"].isin(linked)
        summary["dropped_shared_borrower"][name] = int(shared.sum())
        by[name] = df[~shared].reset_index(drop=True)
    by["test"] = by["test"].reset_index(drop=True)
    summary["rows"] = {k: int(len(v)) for k, v in by.items()}
    summary["positive_rate"] = {k: (round(float(v["label"].mean()), 4) if len(v) else None) for k, v in by.items()}
    for k, v in by.items():
        if len(v) == 0 or v["label"].nunique() < 2:
            raise InvariantError(f"The {k} split needs both outcome classes. Rows: {len(v)}.")
    return by, summary
