"""One as-of date for the whole flow. Same rule as action/homebridge_action/clock.py.

Set HB_AS_OF (ISO 8601, UTC) to make the default clocks read that date. Unset means the real clock.
"""
from __future__ import annotations

import os
from typing import Optional

import pandas as pd

ENV_VAR = "HB_AS_OF"


def as_of() -> Optional[pd.Timestamp]:
    raw = (os.environ.get(ENV_VAR) or "").strip()
    if not raw:
        return None
    try:
        ts = pd.Timestamp(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_VAR} must be an ISO 8601 timestamp such as 2026-10-16T09:00:00Z, not {raw!r}.") from exc
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def now() -> pd.Timestamp:
    return as_of() or pd.Timestamp.now(tz="UTC")
