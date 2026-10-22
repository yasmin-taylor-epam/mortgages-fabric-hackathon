"""Scoring run (notebook 04_score_and_segment, blueprint steps 4 and 9).

Order of work in one run:
1. Write a score_runs row with status running.
2. Load the pinned classifier and cluster versions. Scoring never retrains.
3. Check the snapshots (INV-2, feature version, run cutoff).
4. Score, explain and assign segments.
5. Append account_scores, feature_attributions and segment_membership.
6. Write one risk_outbox row per account with a stable event_id.
7. Mark the run completed. Completion is the visibility gate.

INV-5 / T05: any failure marks the run failed. A failed run writes no outbox
rows, so it publishes no risk_updated event. Rows that a failed run already
appended stay invisible because consumers read completed runs only. The last
completed score then counts as stale (see latest_scores).
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd

from . import clusters, contract, explain, features, gold, models, registry
from .config import RUN_KINDS, TARGET, Paths

ATTRIBUTION_TOP_N = 10
FAILURE_POINTS = ("before_outputs", "after_outputs")


class ScoringRunFailed(RuntimeError):
    def __init__(self, run_id: str, cause: BaseException):
        super().__init__(f"Scoring run {run_id} failed: {cause}")
        self.run_id, self.cause = run_id, cause


class InjectedFailure(RuntimeError):
    """Raised on purpose to demonstrate T05."""


def account_key(mortgage_id, cycle_id) -> str:
    return f"{mortgage_id}|{cycle_id}"


def event_id(run_id: str, mortgage_id: str, cycle_id: str) -> str:
    """Deterministic, so a publication retry reuses the same event_id."""
    return "RU-" + hashlib.sha1(f"{run_id}|{mortgage_id}|{cycle_id}".encode()).hexdigest()[:20]


def new_run_id(paths: Paths, started_at) -> str:
    base = "RUN-" + pd.Timestamp(started_at).tz_convert("UTC").strftime("%Y%m%d-%H%M%S")
    existing = set(gold.read(paths.gold, "score_runs")["run_id"])
    run_id, n = base, 1
    while run_id in existing:
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def _iso(ts) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def score_frame(loaded: dict, snap: pd.DataFrame) -> dict:
    """Pure scoring: no writes. Returns scores, segments and attributions."""
    rel = loaded["release"]
    X = features.build_matrix(snap)
    if list(X.columns) != rel["feature_names"]:
        raise contract.InvariantError("The feature signature differs from the registered model.")
    score = models.proba(loaded["classifier"], X)
    seg = clusters.assign(loaded["segment_model"], loaded["segment_map"], snap)
    contrib = explain.contributions(loaded["classifier"], X)
    keys = snap[["mortgage_id", "cycle_id", "snapshot_id"]].reset_index(drop=True)
    attr = explain.to_long(contrib, X, keys, top_n=ATTRIBUTION_TOP_N)
    attr["contribution_unit"] = explain.UNITS[rel["champion"]]
    return {"score": score, "segment_id": seg.to_numpy(), "attributions": attr}


def run_scoring(paths: Paths, snapshots: pd.DataFrame, run_kind: str, cutoff_at, model_version: str | None = None,
                cluster_model_version: str | None = None, started_at=None, inject_failure: str | None = None) -> dict:
    if run_kind not in RUN_KINDS:
        raise ValueError(f"run_kind must be one of {RUN_KINDS}.")
    if inject_failure not in (None, *FAILURE_POINTS):
        raise ValueError(f"inject_failure must be one of {FAILURE_POINTS}.")
    simulated = started_at is not None
    started_at = pd.Timestamp(started_at) if simulated else pd.Timestamp.now(tz="UTC")

    def clock() -> pd.Timestamp:
        return started_at if simulated else pd.Timestamp.now(tz="UTC")

    cutoff_at = pd.Timestamp(cutoff_at)
    run_id = new_run_id(paths, started_at)
    run = {"run_id": run_id, "run_kind": run_kind, "status": "running", "started_at": started_at, "completed_at": None,
           "cutoff_at": cutoff_at, "model_version": model_version, "cluster_model_version": cluster_model_version,
           "snapshot_count": len(snapshots), "error": None}
    gold.upsert(paths.gold, "score_runs", pd.DataFrame([run]))
    try:
        loaded = registry.load_release(paths, model_version, cluster_model_version)
        rel = loaded["release"]
        run["model_version"], run["cluster_model_version"] = rel["model_version"], rel["cluster_model_version"]
        snap = snapshots.reset_index(drop=True)
        contract.check_snapshots(snap, run_cutoff=cutoff_at, feature_version=rel["feature_version"])
        if inject_failure == "before_outputs":
            raise InjectedFailure("Injected failure before outputs (T05 demo).")
        scored_at = clock()
        res = score_frame(loaded, snap)

        keys = snap["mortgage_id"].astype(str) + "|" + snap["cycle_id"].astype(str)
        scores = pd.DataFrame({
            "account_key": keys, "mortgage_id": snap["mortgage_id"], "cycle_id": snap["cycle_id"], "run_id": run_id,
            "snapshot_id": snap["snapshot_id"], "model_version": rel["model_version"],
            "cluster_model_version": rel["cluster_model_version"], "segment_id": res["segment_id"],
            "score": res["score"].round(6), "scored_at": scored_at, "cutoff_at": snap["cutoff_at"], "target": TARGET})
        attr = res["attributions"].assign(run_id=run_id, model_version=rel["model_version"])
        attr["account_key"] = attr["mortgage_id"] + "|" + attr["cycle_id"]
        segs = pd.DataFrame({
            "account_key": keys, "mortgage_id": snap["mortgage_id"], "cycle_id": snap["cycle_id"], "run_id": run_id,
            "segment_id": res["segment_id"], "cluster_model_version": rel["cluster_model_version"],
            "assigned_at": scored_at, "valid_from": scored_at, "valid_to": None})

        gold.append(paths.gold, "account_scores", scores)
        gold.append(paths.gold, "feature_attributions", attr)
        gold.append(paths.gold, "segment_membership", segs)
        if inject_failure == "after_outputs":
            raise InjectedFailure("Injected failure after outputs, before the outbox (T05 demo).")

        outbox = build_outbox(scores, snap, run_id, scored_at)
        gold.append(paths.gold, "risk_outbox", outbox)
        run.update(status="completed", completed_at=clock())
        gold.upsert(paths.gold, "score_runs", pd.DataFrame([run]))
        return {"run_id": run_id, "status": "completed", "accounts": int(len(scores)), "outbox_rows": int(len(outbox)),
                "model_version": rel["model_version"], "cluster_model_version": rel["cluster_model_version"],
                "model_source": loaded["source"]}
    except Exception as exc:  # noqa: BLE001 - any failure must mark the run failed (INV-5)
        run.update(status="failed", completed_at=None, error=f"{type(exc).__name__}: {exc}"[:500])
        gold.upsert(paths.gold, "score_runs", pd.DataFrame([run]))
        raise ScoringRunFailed(run_id, exc) from exc


def build_outbox(scores: pd.DataFrame, snap: pd.DataFrame, run_id: str, created_at) -> pd.DataFrame:
    from hbdata import contracts as hb_contracts

    evidence = snap.set_index("snapshot_id")["evidence_ids"]
    rows = []
    for r in scores.itertuples(index=False):
        ev = evidence.get(r.snapshot_id)
        payload = {
            "event_id": event_id(run_id, r.mortgage_id, r.cycle_id), "event_type": "risk_updated", "schema_version": "1.0",
            "mortgage_id": r.mortgage_id, "cycle_id": r.cycle_id, "run_id": run_id, "snapshot_id": r.snapshot_id,
            "model_version": r.model_version, "cluster_model_version": r.cluster_model_version, "segment_id": r.segment_id,
            "scored_at": _iso(r.scored_at), "cutoff_at": _iso(r.cutoff_at), "target": TARGET, "score": float(r.score),
            "evidence_ids": [str(x) for x in (list(ev) if ev is not None else [])],
        }
        rows.append(payload)
    errors = hb_contracts.validate_records(rows, "risk_updated")
    if errors:
        raise contract.InvariantError(f"risk_updated payloads break the contract: {errors[:3]}")
    return pd.DataFrame({
        "event_id": [p["event_id"] for p in rows], "run_id": run_id, "mortgage_id": [p["mortgage_id"] for p in rows],
        "cycle_id": [p["cycle_id"] for p in rows], "payload": [json.dumps(p, sort_keys=True) for p in rows],
        "status": "pending", "attempts": 0, "created_at": created_at, "published_at": None, "last_error": None})


def latest_scores(paths: Paths, as_of) -> pd.DataFrame:
    """Latest completed score per account with score_state current or stale.

    Uses the Data and replay score_context rule, so both packages agree: a
    score is stale when a later run failed (T05) or the score is old.
    """
    from hbdata import pipelines_local

    return pipelines_local.score_context(paths.gold, as_of)
