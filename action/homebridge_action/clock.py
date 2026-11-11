"""One as-of date for the whole flow.

The synthetic data stops at one date. Set HB_AS_OF (ISO 8601, UTC) to make every default clock in this package
read that date instead of the real time. With HB_AS_OF unset, the real clock is used, as before.
The default as-of for the demo is 2026-10-16T09:00:00Z (see docs/fabric-deployment.md). Nothing sets it for you.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

ENV_VAR = "HB_AS_OF"


def as_of() -> Optional[datetime]:
    """The configured as-of time (UTC), or None when HB_AS_OF is unset or empty."""
    raw = (os.environ.get(ENV_VAR) or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{ENV_VAR} must be an ISO 8601 timestamp such as 2026-10-16T09:00:00Z, not {raw!r}.") from exc
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def now() -> datetime:
    """The as-of time when it is set, otherwise the real UTC time."""
    return as_of() or datetime.now(timezone.utc)
