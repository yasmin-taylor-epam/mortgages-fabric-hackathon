"""Gold tables written by the Models and evaluation package.

Local stand-in: one Parquet file per table under lakehouse/gold/. In Fabric
these become Lakehouse Delta tables (append for the output tables, MERGE on the
key for the two status tables).

- score_runs, risk_outbox: status tables. A row changes status in place.
- account_scores, feature_attributions, segment_membership: append only. A
  write that would change an existing key fails.

Every output table stores account_key = mortgage_id|cycle_id because Direct
Lake cannot add calculated columns (agreed with Dashboard and action).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

TS = "datetime64[us, UTC]"

TABLES: dict[str, dict] = {
    "score_runs": {
        "key": ["run_id"], "append_only": False,
        "columns": {"run_id": str, "run_kind": str, "status": str, "started_at": TS, "completed_at": TS, "cutoff_at": TS,
                    "model_version": str, "cluster_model_version": str, "snapshot_count": "Int64", "error": str},
    },
    "account_scores": {
        "key": ["mortgage_id", "cycle_id", "run_id"], "append_only": True,
        "columns": {"account_key": str, "mortgage_id": str, "cycle_id": str, "run_id": str, "snapshot_id": str,
                    "model_version": str, "cluster_model_version": str, "segment_id": str, "score": float,
                    "scored_at": TS, "cutoff_at": TS, "target": str},
    },
    "feature_attributions": {
        "key": ["mortgage_id", "cycle_id", "run_id", "feature_name"], "append_only": True,
        "columns": {"account_key": str, "mortgage_id": str, "cycle_id": str, "run_id": str, "snapshot_id": str,
                    "model_version": str, "feature_name": str, "feature_value": float, "contribution": float,
                    "contribution_unit": str, "rank": "Int64"},
    },
    "segment_membership": {
        "key": ["mortgage_id", "cycle_id", "run_id"], "append_only": True,
        "columns": {"account_key": str, "mortgage_id": str, "cycle_id": str, "run_id": str, "segment_id": str,
                    "cluster_model_version": str, "assigned_at": TS, "valid_from": TS, "valid_to": TS},
    },
    "risk_outbox": {
        "key": ["event_id"], "append_only": False,
        "columns": {"event_id": str, "run_id": str, "mortgage_id": str, "cycle_id": str, "payload": str, "status": str,
                    "attempts": "Int64", "created_at": TS, "published_at": TS, "last_error": str},
    },
}


class AppendOnlyError(ValueError):
    """A write would change or duplicate an append-only row."""


def _path(gold_dir: Path, name: str) -> Path:
    return Path(gold_dir) / f"{name}.parquet"


def coerce(df: pd.DataFrame, name: str) -> pd.DataFrame:
    cols = TABLES[name]["columns"]
    out = pd.DataFrame(index=df.index)
    for c, t in cols.items():
        s = df[c] if c in df.columns else pd.Series([None] * len(df), index=df.index, dtype=object)
        if t == TS:
            out[c] = pd.to_datetime(s, utc=True, format="mixed").astype(TS)
        elif t is str:
            out[c] = s.astype(object).where(s.notna(), None)
        else:
            out[c] = s.astype(t)
    return out.reset_index(drop=True)


def read(gold_dir: Path, name: str) -> pd.DataFrame:
    p = _path(gold_dir, name)
    if not p.exists():
        return coerce(pd.DataFrame(), name)
    return coerce(pd.read_parquet(p), name)


def _write(gold_dir: Path, name: str, df: pd.DataFrame) -> None:
    p = _path(gold_dir, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    coerce(df, name).to_parquet(tmp, index=False)
    tmp.replace(p)


def append(gold_dir: Path, name: str, rows: pd.DataFrame) -> int:
    """Append rows. Existing keys never change (append only)."""
    spec = TABLES[name]
    rows = coerce(rows, name)
    if rows.duplicated(spec["key"]).any():
        raise AppendOnlyError(f"{name}: duplicate keys inside one write.")
    old = read(gold_dir, name)
    if len(old):
        clash = rows.merge(old[spec["key"]], on=spec["key"], how="inner")
        if len(clash):
            raise AppendOnlyError(f"{name}: {len(clash)} rows already exist. Append-only rows are never overwritten.")
    _write(gold_dir, name, pd.concat([old, rows], ignore_index=True) if len(old) else rows)
    return int(len(rows))


def upsert(gold_dir: Path, name: str, rows: pd.DataFrame) -> None:
    """Insert or replace rows by key. Only for the status tables."""
    spec = TABLES[name]
    if spec["append_only"]:
        raise AppendOnlyError(f"{name} is append only.")
    rows = coerce(rows, name)
    old = read(gold_dir, name)
    if len(old):
        keep = old.merge(rows[spec["key"]], on=spec["key"], how="left", indicator=True)["_merge"] == "left_only"
        old = old[keep.to_numpy()]
    _write(gold_dir, name, pd.concat([old, rows], ignore_index=True) if len(old) else rows)
