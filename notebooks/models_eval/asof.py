"""One as-of date for the whole flow. Same rule as action/homebridge_action/clock.py.

Set HB_AS_OF (ISO 8601, UTC). A run that is given no start time then starts one minute after the as-of time,
the same gap the local flow uses between a cutoff and its run. Unset means the real clock.
"""
from __future__ import annotations

import os
from typing import Optional

import pandas as pd

ENV_VAR = "HB_AS_OF"
RUN_START_GAP = pd.Timedelta(minutes=1)


def as_of() -> Optional[pd.Timestamp]:
    raw = (os.environ.get(ENV_VAR) or "").strip()
    if not raw:
        return None
    try:
        ts = pd.Timestamp(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_VAR} must be an ISO 8601 timestamp such as 2026-10-16T09:00:00Z, not {raw!r}.") from exc
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def run_start() -> Optional[pd.Timestamp]:
    """The simulated start for a run when HB_AS_OF is set, else None (real clock)."""
    ts = as_of()
    return None if ts is None else ts + RUN_START_GAP


def now() -> pd.Timestamp:
    ts = run_start()
    return ts if ts is not None else pd.Timestamp.now(tz="UTC")
