"""Local runner for pl_scheduled_scoring (blueprint step 9).

Activities, in order:
1. Snapshots. Checkpoint run: the checkpoint snapshots of open deal cycles.
   Scheduled or manual run: live snapshots at the cutoff, built by the Data
   and replay code (notebook 02_feature_snapshots, live mode) from clean
   history plus staged journey results.
2. Score and segment (notebook 04_score_and_segment). Pinned model versions.
3. Publish risk_updated from risk_outbox. Runs only after step 2 succeeds.

A failed step 2 stops the run. No risk_updated event fires (INV-5, T05).
This runner works on local Parquet folders. It has not run in Fabric.
"""
from __future__ import annotations

import pandas as pd

from . import publish, scoring
from .config import Paths

D = pd.Timedelta(days=1)


def checkpoint_snapshots(paths: Paths, cutoff_at) -> pd.DataFrame:
    """Checkpoint snapshots at or before the cutoff whose deal cycle is still open (outcome window not ended)."""
    snap = pd.read_parquet(paths.features / "feature_snapshot.parquet")
    cutoff_at = pd.Timestamp(cutoff_at)
    cp = snap[snap["snapshot_kind"] == "checkpoint"]
    deal_end = cp["cutoff_at"] + cp["days_to_deal_end"] * D
    return cp[(cp["cutoff_at"] <= cutoff_at) & (deal_end + 30 * D >= cutoff_at)].reset_index(drop=True)


def live_snapshots(paths: Paths, cutoff_at, mortgage_ids=None) -> pd.DataFrame:
    """Build live snapshots with the Data and replay code (02_feature_snapshots, live mode)."""
    from hbdata import pipelines_local, snapshots

    clean = pipelines_local.read_clean(paths.lakehouse)
    req = snapshots.live_requests(clean["mortgage_account"], cutoff_at, mortgage_ids)
    staged = pipelines_local.read_staged_events(paths.lakehouse)
    snap = snapshots.build_snapshots(clean, req, cutoff_at, staged)
    snapshots.append_snapshots(snap, paths.features / "feature_snapshot.parquet")
    return snap


def select_snapshots(paths: Paths, cutoff_at, run_kind: str, mortgage_ids=None) -> pd.DataFrame:
    """Snapshots for one run, read from the feature_snapshot table.

    In Fabric, 02_feature_snapshots runs as the activity before scoring, so the
    live snapshots already exist at this cutoff.
    """
    cutoff_at = pd.Timestamp(cutoff_at)
    if run_kind == "checkpoint":
        snap = checkpoint_snapshots(paths, cutoff_at)
    else:
        allsnap = pd.read_parquet(paths.features / "feature_snapshot.parquet")
        snap = allsnap[(allsnap["snapshot_kind"] == "live") & (allsnap["cutoff_at"] == cutoff_at)].reset_index(drop=True)
    if mortgage_ids is not None:
        snap = snap[snap["mortgage_id"].isin(set(mortgage_ids))].reset_index(drop=True)
    return snap


def run(paths: Paths, cutoff_at, run_kind: str = "scheduled", mortgage_ids=None, model_version: str | None = None,
        cluster_model_version: str | None = None, started_at=None, inject_failure: str | None = None, sink=None) -> dict:
    cutoff_at = pd.Timestamp(cutoff_at)
    if run_kind != "checkpoint":
        live_snapshots(paths, cutoff_at, mortgage_ids)
    snap = select_snapshots(paths, cutoff_at, run_kind, mortgage_ids)
    result = {"cutoff_at": cutoff_at.isoformat(), "run_kind": run_kind, "snapshots": int(len(snap))}
    try:
        result["scoring"] = scoring.run_scoring(paths, snap, run_kind, cutoff_at, model_version, cluster_model_version,
                                                started_at=started_at, inject_failure=inject_failure)
    except scoring.ScoringRunFailed as exc:
        result["scoring"] = {"run_id": exc.run_id, "status": "failed", "error": str(exc.cause)}
        result["publish"] = {"skipped": "scoring failed; no risk_updated events (INV-5)"}
        return result
    result["publish"] = publish.publish_pending(paths, sink=sink, now=started_at)
    return result
