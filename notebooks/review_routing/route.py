"""Turn scores and service events into review cases. Notebook 07 calls this module.

The routing rules are not here. They stay in the action package (homebridge_action): the router, the
idempotent case writer and the routing policy. This module only collects the inputs and shapes the output.

Inputs:
- account context rows (hbdata.pipelines_local.account_context: identity, payment support, owner)
- risk_updated events: published risk_outbox rows of the latest completed scoring run
- service events: rows of the KQL function ServiceEvents(), or its local twin hbdata.replay.service_events
- score run statuses: score_runs

Events are processed in arrival order (published_at or ingested_at). The case writer's clock is the event time:
published_at for a risk_updated event (the 24-hour freshness rule uses it), occurred_at for a service event (so a
case opened by a demo event shows the demo time). So the result does not depend on when the notebook runs, and a
rerun gives the same cases. Older scoring runs are left out: the latest completed run is the current score.

Open faults only: a verified portal fault opens a technical case only if the fault is still unresolved at the as-of
time (`open_faults`). The replayed history holds faults from more than a year ago that the customer got past. The
router cannot judge this itself, because its clock is each event's own time. Faults that are left out still get a
decision row (outcome no_case), so the reason stays visible.

`before` keeps only events that happened before a given time. Notebook 07 uses it to build a baseline without the
live demo events.
All data is synthetic.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Optional

import pandas as pd

from homebridge_action.case_writer import CaseWriter
from homebridge_action.models import AccountContext, ContractError, RoutingPolicy, ServiceEvent, candidate_from_dict
from homebridge_action.store import SQLiteCaseStore

CASE_COLUMNS = [
    "case_key", "account_key", "mortgage_id", "cycle_id", "action_family", "status", "assigned_to",
    "case_created_at", "routing_policy", "decision_reason", "source_event_id", "evidence_ids",
    "alert_received_at", "ingested_at", "scored_at", "model_version", "cluster_model_version", "run_id",
    "snapshot_id", "needs_recheck", "priority_rank", "store", "routed_at",
]
DECISION_COLUMNS = [
    "event_id", "event_type", "mortgage_id", "cycle_id", "arrived_at", "outcome", "reason", "case_key",
    "created", "routed_at",
]
TIMESTAMP_COLUMNS = ["case_created_at", "alert_received_at", "ingested_at", "scored_at", "routed_at"]
STEP_COLUMNS = ["application_id", "occurred_at", "status"]

FAULT_EVENT = "portal_fault_verified"
PROGRESS_STATUSES = frozenset({"in_progress", "completed"})  # the customer got further after the fault
CLOSED_STATUSES = frozenset({"completed", "abandoned", "rejected"})  # the application is over
# Reasons on the decision rows of faults that open no case
FAULT_RECOVERED, APPLICATION_CLOSED, DEAL_ENDED = "fault_recovered", "application_closed", "deal_ended"


def _utc(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _evidence(event: dict) -> list:
    ids = event.get("evidence_ids") or []
    return json.loads(ids) if isinstance(ids, str) else list(ids)


def before(rows: Iterable[dict], events_before: Any) -> list[dict]:
    """Rows whose occurred_at is strictly before `events_before`. None or empty text keeps every row.

    It uses occurred_at, not ingested_at: the replayed history arrived on the real clock, after the demo time."""
    rows = list(rows)
    if events_before is None or (isinstance(events_before, str) and not events_before.strip()):
        return rows
    cut = _utc(events_before)
    return [r for r in rows if _utc(r["occurred_at"]) < cut]


def transfer_steps(rows: Iterable[dict]) -> pd.DataFrame:
    """Renewal steps (transfer_request_event rows) as application_id, occurred_at, status.

    A row may carry `status` itself (a KQL projection) or a `payload` (JSON text or a dict) that holds it."""
    out = []
    for row in rows:
        status = row.get("status")
        if status is None:
            payload = row.get("payload") or {}
            payload = json.loads(payload) if isinstance(payload, str) else payload
            status = payload.get("status")
        out.append({"application_id": str(row["application_id"]), "occurred_at": _utc(row["occurred_at"]),
                    "status": status})
    return pd.DataFrame(out, columns=STEP_COLUMNS)


def deal_ends(context: pd.DataFrame) -> dict[tuple[str, str], pd.Timestamp]:
    """deal_end_at by (mortgage_id, cycle_id), from the account context."""
    if "deal_end_at" not in context.columns:
        return {}
    return {(str(m), str(c)): _utc(d) for m, c, d in zip(context["mortgage_id"], context["cycle_id"], context["deal_end_at"])
            if pd.notna(d)}


def open_faults(service_rows: Iterable[dict], steps: pd.DataFrame, ends: dict[tuple[str, str], pd.Timestamp],
                as_of: Any) -> tuple[list[dict], list[dict]]:
    """Keep a verified portal fault only if it is still unresolved at `as_of`. Return (kept rows, left-out rows).

    Faults are judged per mortgage and deal cycle (the technical case key), by the latest fault and its application:
    - fault_recovered: a later step on the same application went on (in_progress or completed);
    - application_closed: the application's latest step is completed, abandoned or rejected;
    - deal_ended: the deal ended on or before `as_of`.
    Otherwise every fault of that key is kept, and the case writer opens one technical case. Other service events
    pass through unchanged. A fault with no account context passes through, so the router quarantines it."""
    rows = [dict(r) for r in service_rows]
    as_of = _utc(as_of)
    faults: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if r.get("event_type") == FAULT_EVENT:
            faults.setdefault((str(r["mortgage_id"]), str(r.get("cycle_id"))), []).append(r)
    steps = steps if steps is not None else pd.DataFrame(columns=STEP_COLUMNS)
    by_app = {app: g.sort_values("occurred_at") for app, g in steps.groupby("application_id")} if len(steps) else {}
    reasons: dict[tuple[str, str], str] = {}
    for key, items in faults.items():
        last = max(items, key=lambda r: (_utc(r["occurred_at"]), str(r["event_id"])))
        at, ids = _utc(last["occurred_at"]), _evidence(last)
        app = by_app.get(str(ids[1])) if len(ids) > 1 else None
        if app is not None and ((app["occurred_at"] > at) & app["status"].isin(PROGRESS_STATUSES)).any():
            reasons[key] = FAULT_RECOVERED
        elif app is not None and app.iloc[-1]["status"] in CLOSED_STATUSES:
            reasons[key] = APPLICATION_CLOSED
        elif key in ends and ends[key] <= as_of:
            reasons[key] = DEAL_ENDED
    kept, left_out = [], []
    for r in rows:
        key = (str(r["mortgage_id"]), str(r.get("cycle_id")))
        if r.get("event_type") == FAULT_EVENT and key in reasons:
            left_out.append({"event_id": str(r["event_id"]), "event_type": FAULT_EVENT, "mortgage_id": key[0],
                             "cycle_id": key[1], "arrived_at": _utc(r["ingested_at"]), "reason": reasons[key]})
        else:
            kept.append(r)
    return kept, left_out


def latest_completed_run(score_runs: pd.DataFrame) -> Optional[str]:
    done = score_runs[score_runs["status"] == "completed"]
    if done.empty:
        return None
    done = done.assign(_at=pd.to_datetime(done["completed_at"], utc=True))
    return str(done.sort_values(["_at", "run_id"]).iloc[-1]["run_id"])


def risk_candidates(outbox: pd.DataFrame, run_id: Optional[str]) -> list[tuple[pd.Timestamp, str, dict]]:
    """Published risk_updated payloads of one run, with their arrival time (published_at)."""
    if run_id is None or outbox.empty:
        return []
    rows = outbox[(outbox["status"] == "published") & (outbox["run_id"] == run_id)]
    out = []
    for published_at, payload in zip(rows["published_at"], rows["payload"]):
        event = json.loads(payload)
        out.append((_utc(published_at), str(event["event_id"]), event))
    return out


def service_candidates(events: Iterable[dict]) -> list[tuple[pd.Timestamp, str, dict]]:
    """ServiceEvents() rows, with their arrival time (ingested_at). evidence_ids may arrive as a JSON string."""
    out = []
    for row in events:
        event = dict(row)
        if isinstance(event.get("evidence_ids"), str):
            event["evidence_ids"] = json.loads(event["evidence_ids"])
        for key in ("occurred_at", "ingested_at"):
            event[key] = _utc(event[key]).isoformat()
        out.append((_utc(event["ingested_at"]), str(event["event_id"]), event))
    return out


def contexts(context: pd.DataFrame) -> dict[tuple[str, str], AccountContext]:
    rows = json.loads(context.to_json(orient="records", date_format="iso"))
    out = {}
    for row in rows:
        ctx = AccountContext.from_dict(row)
        out[(ctx.mortgage_id, ctx.cycle_id)] = ctx
    return out


def route(accounts: dict[tuple[str, str], AccountContext], candidates: list[tuple[pd.Timestamp, str, dict]],
          runs: dict[str, str], policy: RoutingPolicy, routed_at: Any,
          left_out: Optional[list[dict]] = None) -> dict[str, Any]:
    """Run every candidate through the case writer in arrival order. Return case rows and decision rows.

    `left_out` holds the faults that `open_faults` dropped. Each gets a decision row (no_case, with its reason)."""
    routed_at = _utc(routed_at)
    clock_now: list[datetime] = [routed_at.to_pydatetime()]
    store = SQLiteCaseStore(":memory:")
    writer = CaseWriter(store, context_lookup=lambda m, c: accounts.get((m, c)), run_status_lookup=runs.get,
                        policy=policy, clock=lambda: clock_now[0])
    decisions, rejected = [], []
    for arrived_at, event_id, event in sorted(candidates, key=lambda t: (t[0], t[1])):
        try:
            candidate = candidate_from_dict(event)
        except (ContractError, ValueError) as exc:
            rejected.append({"event_id": event_id, "error": str(exc)})
            continue
        clock_now[0] = candidate.occurred_at if isinstance(candidate, ServiceEvent) else arrived_at.to_pydatetime()
        result = writer.handle(candidate)
        decisions.append({
            "event_id": candidate.event_id, "event_type": event.get("event_type", ""),
            "mortgage_id": candidate.mortgage_id, "cycle_id": candidate.cycle_id, "arrived_at": arrived_at,
            "outcome": result.decision.outcome.value, "reason": result.decision.reason,
            "case_key": result.case.case_key if result.case else None, "created": bool(result.created),
            "routed_at": routed_at,
        })
    for row in left_out or []:
        decisions.append({**row, "outcome": "no_case", "case_key": None, "created": False, "routed_at": routed_at})
    cases = []
    for c in store.list_cases():
        cases.append({
            "case_key": c.case_key, "account_key": f"{c.mortgage_id}|{c.cycle_id}", "mortgage_id": c.mortgage_id,
            "cycle_id": c.cycle_id, "action_family": c.action_family.value, "status": c.status.value,
            "assigned_to": c.assigned_to, "case_created_at": c.case_created_at, "routing_policy": c.routing_policy,
            "decision_reason": c.decision_reason, "source_event_id": c.source_event_id,
            "evidence_ids": json.dumps(list(c.evidence_ids)), "alert_received_at": c.alert_received_at,
            "ingested_at": c.ingested_at, "scored_at": c.scored_at, "model_version": c.model_version,
            "cluster_model_version": c.cluster_model_version, "run_id": c.run_id, "snapshot_id": c.snapshot_id,
            "needs_recheck": bool(c.needs_recheck), "priority_rank": c.priority_rank, "store": "fabric",
            "routed_at": routed_at,
        })
    exceptions = store.list_exceptions()
    store.close()
    case_df = pd.DataFrame(cases, columns=CASE_COLUMNS)
    for col in TIMESTAMP_COLUMNS:
        case_df[col] = pd.to_datetime(case_df[col], utc=True)
    case_df["priority_rank"] = case_df["priority_rank"].astype("Int64")
    decision_df = pd.DataFrame(decisions, columns=DECISION_COLUMNS)
    for col in ("arrived_at", "routed_at"):
        decision_df[col] = pd.to_datetime(decision_df[col], utc=True)
    return {"cases": case_df, "decisions": decision_df, "rejected": rejected,
            "exceptions": [{"kind": e["kind"], "event_id": e["event_id"], "message": e["message"]} for e in exceptions]}


def summary(result: dict[str, Any], run_id: Optional[str], service_event_count: int, routed_at: Any) -> dict[str, Any]:
    cases, decisions = result["cases"], result["decisions"]
    return {
        "routed_at": _utc(routed_at).isoformat(),
        "score_run_id": run_id,
        "service_events": int(service_event_count),
        "decisions": {f"{o}:{r}": int(n) for (o, r), n in decisions.groupby(["outcome", "reason"]).size().items()},
        "cases": int(len(cases)),
        "cases_by_family": {k: int(v) for k, v in cases["action_family"].value_counts().items()},
        "rejected_events": len(result["rejected"]),
        "exceptions": len(result["exceptions"]),
    }
