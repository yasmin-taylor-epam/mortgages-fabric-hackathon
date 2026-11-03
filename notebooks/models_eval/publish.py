"""Publish risk_updated events from risk_outbox (pl_scheduled_scoring, publish step).

- Only rows from completed runs are sent (INV-5). A failed run has no outbox
  rows, and this filter is a second guard.
- A retry resends the stored payload. It reuses the same event_id and never
  rescores.
- Delivery is at least once. The case writer deduplicates by event_id and by
  the review-case key (T01, T06).

Locally the sink appends JSON lines to data/output/eventstream/risk_updated.jsonl.
In Fabric the sink would be an Eventstream custom endpoint. That connection is
not built or tested yet.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import gold
from .config import Paths


class JsonlSink:
    def __init__(self, path: Path):
        self.path = Path(path)

    def send(self, events: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, sort_keys=True) + "\n")


def default_sink(paths: Paths) -> JsonlSink:
    return JsonlSink(paths.eventstream / "risk_updated.jsonl")


def publish_pending(paths: Paths, sink=None, now=None, batch_size: int = 500) -> dict:
    sink = sink or default_sink(paths)
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    outbox = gold.read(paths.gold, "risk_outbox")
    runs = gold.read(paths.gold, "score_runs")
    completed = set(runs.loc[runs["status"] == "completed", "run_id"])
    pending = outbox[(outbox["status"] == "pending") & outbox["run_id"].isin(completed)]
    blocked = outbox[(outbox["status"] == "pending") & ~outbox["run_id"].isin(completed)]
    sent, failed, errors = 0, 0, []
    for start in range(0, len(pending), batch_size):
        idx = pending.index[start:start + batch_size]
        events = [json.loads(p) for p in outbox.loc[idx, "payload"]]
        outbox.loc[idx, "attempts"] = outbox.loc[idx, "attempts"].fillna(0) + 1
        try:
            sink.send(events)
        except Exception as exc:  # noqa: BLE001 - keep the row pending and visible for retry
            outbox.loc[idx, "last_error"] = f"{type(exc).__name__}: {exc}"[:500]
            failed += len(idx)
            errors.append(str(exc))
            continue
        outbox.loc[idx, "status"] = "published"
        outbox.loc[idx, "published_at"] = now
        outbox.loc[idx, "last_error"] = None
        sent += len(idx)
    if len(pending):
        gold.upsert(paths.gold, "risk_outbox", outbox.loc[pending.index])
    return {"published": sent, "failed": failed, "not_from_completed_run": int(len(blocked)), "errors": errors[:3]}
