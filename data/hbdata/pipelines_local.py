"""Local stand-ins for the Data and replay Fabric pipelines.

- pl_load_raw: dated exports -> append-only raw Lakehouse tables (step 1).
- pl_copy_context: clean history and completed score summaries -> Eventhouse context copies (step 7).
- pl_export_journeys: cutoff-filtered journey results -> Lakehouse staging tables (step 8).

These run on Parquet folders. They do not run in Fabric. pipelines/*/README.md
describes the Fabric design for each one.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from . import contracts, io, journeys, replay
from .validation import SOURCE_DATASETS, case_status_at

SCORE_STALE_HOURS = 24


def _stamp(ts) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y%m%dT%H%M%SZ")


# ------------------------------------------------------------ pl_load_raw

def load_raw(exports_dir: Path, lakehouse_dir: Path, loaded_at) -> dict:
    """Append each export as a new load. Raw tables never overwrite earlier loads."""
    exports_dir, raw_dir = Path(exports_dir), Path(lakehouse_dir) / "raw"
    load_id = f"load-{_stamp(loaded_at)}"
    log_path = raw_dir / "_loads.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    entry = {"load_id": load_id, "loaded_at": pd.Timestamp(loaded_at).isoformat(), "files": {}}
    for name in SOURCE_DATASETS:
        src = exports_dir / f"{name}.parquet"
        if not src.exists():
            continue
        digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
        if any(l["files"].get(name, {}).get("sha256_16") == digest for l in log):
            entry["files"][name] = {"skipped": "same file already loaded", "sha256_16": digest}
            continue
        df = io.read_table(src)
        io.write_table(df, raw_dir / name / f"{load_id}.parquet", name)
        entry["files"][name] = {"rows": int(len(df)), "sha256_16": digest}
    log.append(entry)
    io.write_json(log, log_path)
    return entry


def read_raw(lakehouse_dir: Path) -> dict[str, pd.DataFrame]:
    raw_dir = Path(lakehouse_dir) / "raw"
    out = {}
    for name in SOURCE_DATASETS:
        parts = sorted((raw_dir / name).glob("*.parquet")) if (raw_dir / name).exists() else []
        frames = [io.read_table(p) for p in parts]
        out[name] = io.coerce(pd.concat(frames, ignore_index=True), name) if frames else io.coerce(pd.DataFrame(), name)
    return out


def read_clean(lakehouse_dir: Path) -> dict[str, pd.DataFrame]:
    d = Path(lakehouse_dir) / "clean"
    return {name: io.read_table(d / f"{name}.parquet") for name in SOURCE_DATASETS}


# ------------------------------------------------------------ pl_copy_context

def account_context(clean: dict[str, pd.DataFrame], conflict_mortgages, watermarks: pd.DataFrame, copied_at) -> pd.DataFrame:
    copied_at = pd.Timestamp(copied_at)
    acct = clean["mortgage_account"]
    st = case_status_at(clean["servicing_case_event"], copied_at)
    active = set(st[(st["category"] == "payment_difficulty") & (st["latest_status"] != "resolved")]["mortgage_id"])
    svc_stale = bool(watermarks.set_index("dataset").loc["servicing_case_event", "is_stale"]) if "servicing_case_event" in set(watermarks["dataset"]) else True
    ctx = pd.DataFrame({
        "mortgage_id": acct["mortgage_id"], "cycle_id": acct["cycle_id"], "deal_start_at": acct["deal_start_at"],
        "deal_end_at": acct["deal_end_at"], "checkpoint_at": acct["deal_end_at"] - pd.Timedelta(days=90),
        "identity_status": ["conflict" if m in set(conflict_mortgages) else "ok" for m in acct["mortgage_id"]],
        "payment_support_status": ["unknown" if svc_stale else ("active" if m in active else "none") for m in acct["mortgage_id"]],
        "owner_id": acct["owner_id"], "context_version": f"ctx-{_stamp(copied_at)}", "copied_at": copied_at,
    })
    return io.coerce(ctx, "account_context")


def score_context(gold_dir: Path, copied_at) -> pd.DataFrame:
    """Latest completed score per mortgage and cycle. A score is stale when a later run failed (T05) or it is old."""
    cols = ["mortgage_id", "cycle_id", "run_id", "snapshot_id", "model_version", "score", "scored_at", "cutoff_at",
            "score_state", "copied_at"]
    gold_dir, copied_at = Path(gold_dir), pd.Timestamp(copied_at)
    scores_p, runs_p = gold_dir / "account_scores.parquet", gold_dir / "score_runs.parquet"
    if not scores_p.exists() or not runs_p.exists():
        return pd.DataFrame(columns=cols)
    scores, runs = pd.read_parquet(scores_p), pd.read_parquet(runs_p)
    done = runs[runs["status"] == "completed"]
    s = scores[scores["run_id"].isin(set(done["run_id"]))].sort_values("scored_at")
    s = s.groupby(["mortgage_id", "cycle_id"], as_index=False).last()
    failed = runs[runs["status"] == "failed"]
    last_failed = pd.to_datetime(failed["started_at"], utc=True).max() if len(failed) else pd.NaT
    s["scored_at"] = pd.to_datetime(s["scored_at"], utc=True)
    later_failure = s["scored_at"] < last_failed if pd.notna(last_failed) else pd.Series(False, index=s.index)
    old = (copied_at - s["scored_at"]) > pd.Timedelta(hours=SCORE_STALE_HOURS)
    s["score_state"] = ["stale" if (f or o) else "current" for f, o in zip(later_failure, old)]
    s["copied_at"] = copied_at
    return s[cols].reset_index(drop=True)


def copy_context(lakehouse_dir: Path, eventhouse_dir: Path, copied_at, gold_dir: Path | None = None) -> dict:
    lakehouse_dir, eventhouse_dir = Path(lakehouse_dir), Path(eventhouse_dir)
    clean = read_clean(lakehouse_dir)
    report = json.loads((lakehouse_dir / "clean" / "_validation_report.json").read_text(encoding="utf-8"))
    wm = io.read_table(lakehouse_dir / "clean" / "feed_watermark.parquet")
    ctx = account_context(clean, report["conflict_mortgages"], wm, copied_at)
    io.write_table(ctx, eventhouse_dir / "account_context.parquet", "account_context")
    sc = score_context(gold_dir or lakehouse_dir / "gold", copied_at)
    eventhouse_dir.mkdir(parents=True, exist_ok=True)
    sc.to_parquet(eventhouse_dir / "score_context.parquet", index=False)
    io.write_table(wm, eventhouse_dir / "source_watermarks.parquet", "feed_watermark")
    return {"account_context_rows": int(len(ctx)), "score_context_rows": int(len(sc)),
            "context_version": ctx["context_version"].iloc[0] if len(ctx) else None,
            "note": None if len(sc) else "No completed score summaries found in the gold folder."}


# ------------------------------------------------------------ pl_export_journeys

def export_journeys(eventhouse_dir: Path, lakehouse_dir: Path, cutoff_at, exported_at) -> dict:
    """Run the three journey queries at one cutoff and stage the results with a watermark."""
    cutoff_at, exported_at = pd.Timestamp(cutoff_at), pd.Timestamp(exported_at)
    raw, _ = replay.read_local(eventhouse_dir)
    tr = replay.transfer_events(raw)
    export_id = f"jrn-{_stamp(cutoff_at)}"
    out = {
        "stg_journey_timeline": journeys.stage_timeline(tr, cutoff_at),
        "stg_stage_dwell": journeys.stage_dwell(tr, cutoff_at),
        "stg_retries_after_error": journeys.retries_after_error(tr, cutoff_at),
    }
    known = tr[(tr["occurred_at"] <= cutoff_at) & (tr["ingested_at"] <= cutoff_at)] if len(tr) else tr
    watermark = known["ingested_at"].max() if len(known) else None
    stage_dir = Path(lakehouse_dir) / "staging"
    stage_dir.mkdir(parents=True, exist_ok=True)
    for name, df in out.items():
        df = df.copy()
        df["export_id"], df["cutoff_at"], df["source_watermark"], df["exported_at"] = export_id, cutoff_at, watermark, exported_at
        df.to_parquet(stage_dir / f"{name}.parquet", index=False)
    io.write_json({"export_id": export_id, "cutoff_at": cutoff_at.isoformat(), "source_watermark": None if watermark is None else pd.Timestamp(watermark).isoformat(),
                   "rows": {k: int(len(v)) for k, v in out.items()}}, stage_dir / "_export.json")
    return {"export_id": export_id, "rows": {k: int(len(v)) for k, v in out.items()}}


def read_staged_events(lakehouse_dir: Path) -> pd.DataFrame:
    """Staged journey timeline rows as transfer_request_event rows, for live snapshots."""
    p = Path(lakehouse_dir) / "staging" / "stg_journey_timeline.parquet"
    if not p.exists():
        return pd.DataFrame(columns=contracts.columns("transfer_request_event"))
    df = pd.read_parquet(p)
    df["channel"] = df.get("channel", "portal")
    return io.coerce(df, "transfer_request_event")
