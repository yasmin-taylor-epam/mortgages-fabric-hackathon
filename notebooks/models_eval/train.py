"""Training for one model release (notebook 03_train_models, blueprint step 3)."""
from __future__ import annotations

import pandas as pd

from . import clusters, contract, features, models, registry
from .config import REVIEWS_PER_RUN, TARGET, Paths, SplitConfig


def load_inputs(paths: Paths) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    snap = pd.read_parquet(paths.features / "feature_snapshot.parquet")
    outcomes = pd.read_parquet(paths.features / "observed_outcome.parquet")
    party_p = paths.clean / "account_party.parquet"
    parties = pd.read_parquet(party_p) if party_p.exists() else None
    return snap, outcomes, parties


def prepare(paths: Paths, cfg: SplitConfig) -> dict:
    snap, outcomes, parties = load_inputs(paths)
    contract.check_snapshots(snap, feature_version=features.FEATURE_VERSION)
    frame = contract.labelled_checkpoints(snap, outcomes)
    parts, summary = contract.split(frame, parties, cfg)
    X = {k: features.build_matrix(v) for k, v in parts.items()}
    y = {k: v["label"].astype(int).to_numpy() for k, v in parts.items()}
    return {"snapshots": snap, "outcomes": outcomes, "parts": parts, "split_summary": summary, "X": X, "y": y}


def train_release(paths: Paths, cfg: SplitConfig | None = None, created_at=None, use_mlflow: bool = True,
                  n_boot: int = 20) -> dict:
    cfg = cfg or SplitConfig()
    created_at = pd.Timestamp(created_at) if created_at is not None else pd.Timestamp.now(tz="UTC")
    data = prepare(paths, cfg)
    X, y, parts = data["X"], data["y"], data["parts"]

    tuned = models.tune(X["train"], y["train"], X["validation"], y["validation"], seed=cfg.seed % 1000)
    thresholds = {name: models.freeze_threshold(y["validation"], models.proba(t["model"], X["validation"])) for name, t in tuned.items()}
    champion = models.choose_champion(tuned)

    seg = clusters.fit_segments(parts["train"], seed=cfg.seed % 1000, n_boot=n_boot)
    seg_ids = clusters.assign(seg["model"], seg["segment_map"], parts["train"])
    prof = clusters.profiles(parts["train"], seg_ids, parts["train"]["label"])

    release = {
        "release_id": registry.release_id(created_at),
        "created_at": created_at.isoformat(),
        "synthetic_data": True,
        "target": TARGET,
        "feature_version": features.FEATURE_VERSION,
        "feature_names": features.feature_names(),
        "excluded_features": features.EXCLUDED + [f"{p}*" for p in features.EXCLUDED_PREFIXES],
        "champion": champion,
        "params": {name: t["params"] for name, t in tuned.items()},
        "validation": {name: {k: v for k, v in t.items() if k not in ("model", "params")} | thresholds[name]
                       for name, t in tuned.items()},
        "alert_threshold": thresholds[champion]["threshold"],
        "alert_threshold_note": "Candidate frozen on validation data (max F1). The team confirms the routing threshold.",
        "review_capacity_threshold": round(float(sorted(models.proba(tuned[champion]["model"], X["validation"]), reverse=True)[
            min(REVIEWS_PER_RUN, len(X["validation"])) - 1]), 4),
        "review_capacity_note": f"Score of the {REVIEWS_PER_RUN}th highest validation account. Matches ten reviews per checkpoint cohort.",
        "split": cfg.to_json() | data["split_summary"],
        "segments": {"k": seg["k"], "features": seg["features"], "selection": seg["selection"].to_dict("records"),
                     "segment_map": {str(k): v for k, v in seg["segment_map"].items()},
                     "profiles": prof.to_dict("records"),
                     "names": "Not assigned. The team names segments after inspecting the profiles."},
    }
    candidates = {name: t["model"] for name, t in tuned.items()}
    return registry.register_release(paths, release, candidates, seg["model"], use_mlflow=use_mlflow)
