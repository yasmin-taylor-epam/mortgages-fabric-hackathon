"""Build the events that the customer portal sends. They follow the live_event contract in data/contracts.

The demo clock: the synthetic data ends on 15 Oct 2026. The first event the portal sends happens at demo_start.
Each later event happens demo_step_minutes after the latest live event already in the Eventhouse. So the story
moves forward in time without the real clock, and a restart of the portal does not reset it.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pandas as pd

from .config import Settings
from .sources import LIVE_RANGE, check_mortgage_id

ACTIONS = {
    "renew": "Renew my deal",
    "payment_help": "I need help with payments",
}


def _iso(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.000Z")


def next_demo_time(settings: Settings, last_live_occurred: str | None) -> pd.Timestamp:
    start = pd.Timestamp(settings.demo_start)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    if not last_live_occurred:
        return start
    last = pd.Timestamp(last_live_occurred)
    last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
    return max(start, last + timedelta(minutes=settings.demo_step_minutes))


def _digits(n: int) -> str:
    return f"{uuid.uuid4().int % 10 ** n:0{n}d}"


def new_id(kind: str) -> str:
    """Portal events use the number range 97xxxxxxx, so every page can tell them apart from the synthetic history."""
    return f"{kind}-{LIVE_RANGE}{_digits(7)}"


def build(action: str, mortgage_id: str, occurred_at: pd.Timestamp, application_id: str | None,
          stage: str | None, fault: bool = False) -> dict:
    """One live_event envelope. A renewal step goes to the renewal portal feed. A payment-help request goes to the
    servicing feed. `fault` makes the renewal step fail with a checked software fault (SYS-503)."""
    mid = check_mortgage_id(mortgage_id)
    at = _iso(occurred_at)
    sent = at  # the portal sends at once, as the replay does
    if action == "renew":
        payload = {"stage": stage or "product_selected", "status": "error" if fault else "in_progress",
                   "error_code": "SYS-503" if fault else None, "error_class": "software_fault" if fault else None,
                   "fault_verified": True if fault else None, "channel": "portal"}
        return {"event_id": new_id("PTE"), "event_type": "transfer_request_event", "schema_version": "1.0",
                "source_system": "renewal_portal", "mortgage_id": mid,
                "application_id": application_id or f"PTR-{LIVE_RANGE}{_digits(5)}",
                "occurred_at": at, "sent_at": sent, "payload": payload}
    if action == "payment_help":
        payload = {"case_id": f"CASE-{LIVE_RANGE}{_digits(5)}", "support_route": "payment_support",
                   "channel": "secure_message"}  # a message sent from the customer website
        return {"event_id": new_id("PSE"), "event_type": "payment_support_requested", "schema_version": "1.0",
                "source_system": "servicing", "mortgage_id": mid, "application_id": None,
                "occurred_at": at, "sent_at": sent, "payload": payload}
    raise ValueError(f"Unknown action '{action}'.")


def validate(envelope: dict) -> list[str]:
    """Check the envelope against the live_event contract before it leaves the laptop."""
    from hbdata import contracts
    return contracts.validate_records([envelope], "live_event", 5)
