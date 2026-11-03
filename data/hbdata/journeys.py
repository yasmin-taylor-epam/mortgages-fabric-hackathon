"""Journey queries: the pandas twin of kql/functions/journeys.kql.

Each function takes product-transfer request events and returns the same
columns as its KQL counterpart. Keep the two in step. The tests check the
twin; the KQL is not yet run in Fabric.

Rules shared with the KQL:
- Keep only events known at the cutoff: occurred_at and ingested_at <= cutoff (INV-2).
- Deduplicate by event_id, keeping the earliest ingestion (T01).
- A stage visit starts when the stage differs from the previous event's stage.
- A retry is an in_progress event after a software fault in the same stage visit.
"""
from __future__ import annotations

import pandas as pd

TERMINAL = {"completed", "abandoned", "rejected"}
TIMELINE_COLS = ["application_id", "mortgage_id", "seq", "event_id", "occurred_at", "ingested_at", "stage", "status",
                 "error_code", "error_class", "fault_verified", "channel"]


def known_at(events: pd.DataFrame, cutoff=None, keys: tuple[str, ...] = ()) -> pd.DataFrame:
    """Filter to events known at the cutoff and drop replayed duplicates.

    cutoff can be a scalar or None. If None and the frame has a cutoff_at column, each row uses its own cutoff.
    """
    ev = events
    if cutoff is not None:
        cutoff = pd.Timestamp(cutoff)
        ev = ev[(ev["occurred_at"] <= cutoff) & (ev["ingested_at"] <= cutoff)]
    elif "cutoff_at" in ev.columns:
        ev = ev[(ev["occurred_at"] <= ev["cutoff_at"]) & (ev["ingested_at"] <= ev["cutoff_at"])]
    ev = ev.sort_values([*keys, "event_id", "ingested_at"]).drop_duplicates([*keys, "event_id"], keep="first")
    return ev.reset_index(drop=True)


def _flag(ev: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    t = ev.sort_values([*keys, "occurred_at", "ingested_at", "event_id"]).reset_index(drop=True)
    g = t.groupby(keys, sort=False)
    t["seq"] = g.cumcount() + 1
    t["visit"] = (t["stage"] != g["stage"].shift()).astype(int).groupby([t[k] for k in keys]).cumsum()
    t["is_software_fault"] = (t["error_class"] == "software_fault").fillna(False).astype(bool)
    vg = t.groupby([*keys, "visit"], sort=False)["is_software_fault"]
    t["fault_before"] = (vg.cumsum() - t["is_software_fault"].astype(int)) > 0
    t["is_retry"] = (t["status"] == "in_progress") & t["fault_before"]
    return t


def stage_timeline(events: pd.DataFrame, cutoff=None, keys: tuple[str, ...] = ("application_id",)) -> pd.DataFrame:
    """Query 1: each request's stages and portal errors in time order."""
    ev = known_at(events, cutoff, tuple(k for k in keys if k != "application_id"))
    if ev.empty:
        return pd.DataFrame(columns=[*[k for k in keys if k != "application_id"], *TIMELINE_COLS])
    t = _flag(ev, list(keys))
    extra = [k for k in keys if k != "application_id"]
    return t[[*extra, *TIMELINE_COLS]]


def stage_dwell(events: pd.DataFrame, cutoff=None, keys: tuple[str, ...] = ("application_id",)) -> pd.DataFrame:
    """Query 2: days that a request remains at one stage. An open visit runs to the cutoff."""
    extra = tuple(k for k in keys if k != "application_id")
    ev = known_at(events, cutoff, extra)
    cols = [*extra, "application_id", "mortgage_id", "visit", "stage", "entered_at", "left_at", "dwell_days", "is_open", "events", "errors"]
    if ev.empty:
        return pd.DataFrame(columns=cols)
    t = _flag(ev, list(keys))
    if cutoff is not None:
        t["cutoff_at"] = pd.Timestamp(cutoff)
    elif "cutoff_at" not in t.columns:
        t["cutoff_at"] = pd.NaT
    v = t.groupby([*keys, "visit"], as_index=False, sort=False).agg(
        mortgage_id=("mortgage_id", "first"), stage=("stage", "first"), entered_at=("occurred_at", "min"),
        last_event_at=("occurred_at", "max"), last_status=("status", "last"), events=("event_id", "count"),
        errors=("status", lambda s: int((s == "error").sum())), cutoff_at=("cutoff_at", "first"))
    v["next_entered_at"] = v.groupby(list(keys), sort=False)["entered_at"].shift(-1)
    terminal = v["last_status"].isin(TERMINAL)
    v["is_open"] = v["next_entered_at"].isna() & ~terminal
    v["left_at"] = v["next_entered_at"].fillna(v["last_event_at"].where(terminal, v["cutoff_at"]))
    v["dwell_days"] = (v["left_at"] - v["entered_at"]).dt.total_seconds() / 86400
    return v[cols]


def retries_after_error(events: pd.DataFrame, cutoff=None, keys: tuple[str, ...] = ("application_id",)) -> pd.DataFrame:
    """Query 3: retries after a renewal-portal software error, per request."""
    extra = tuple(k for k in keys if k != "application_id")
    ev = known_at(events, cutoff, extra)
    cols = [*extra, "application_id", "mortgage_id", "software_errors", "verified_faults", "retries_after_error",
            "last_software_error_at", "recovered"]
    if ev.empty:
        return pd.DataFrame(columns=cols)
    t = _flag(ev, list(keys))
    t["is_verified"] = t["is_software_fault"] & (t["fault_verified"] == True)  # noqa: E712
    t["fault_visit"] = t["visit"].where(t["is_software_fault"])
    t["fault_at"] = t["occurred_at"].where(t["is_software_fault"])
    r = t.groupby(list(keys), as_index=False, sort=False).agg(
        mortgage_id=("mortgage_id", "first"), software_errors=("is_software_fault", "sum"),
        verified_faults=("is_verified", "sum"), retries_after_error=("is_retry", "sum"),
        last_software_error_at=("fault_at", "max"), last_fault_visit=("fault_visit", "max"), max_visit=("visit", "max"))
    r = r[r["software_errors"] > 0].copy()
    r["recovered"] = r["max_visit"] > r["last_fault_visit"]
    for c in ("software_errors", "verified_faults", "retries_after_error"):
        r[c] = r[c].astype(int)
    return r[cols].reset_index(drop=True)


def flagged_events(events: pd.DataFrame, keys: tuple[str, ...]) -> pd.DataFrame:
    """Event-level flags (visit, software fault, retry) for feature building. Events must already be known_at."""
    return _flag(events, list(keys))
