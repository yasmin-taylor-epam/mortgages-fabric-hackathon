"""Replay candidate events through the local review-case writer.

Example:
    python -m homebridge_action.cli --events tests/fixtures/replay.jsonl \
        --context tests/fixtures/context.json --policy tests/fixtures/policy.json \
        --db cases.sqlite --now 2026-11-03T10:00:00Z

The output is a JSON summary of cases, audit actions and exceptions. All cases
show store = "local". This run does not write to SharePoint.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from .case_writer import CaseWriter
from .models import AccountContext, ContractError, RoutingPolicy, candidate_from_dict, parse_ts
from .store import SQLiteCaseStore


def load_context(path: Path) -> tuple[dict[tuple[str, str], AccountContext], dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    accounts = {}
    for row in data.get("accounts", []):
        ctx = AccountContext.from_dict(row)
        accounts[(ctx.mortgage_id, ctx.cycle_id)] = ctx
    return accounts, {str(k): str(v) for k, v in data.get("runs", {}).items()}


def replay(events_path: Path, context_path: Path, policy_path: Path, db: str, now: Optional[str]) -> dict[str, Any]:
    accounts, runs = load_context(context_path)
    policy = RoutingPolicy.from_dict(json.loads(policy_path.read_text(encoding="utf-8")))
    fixed_now = parse_ts(now, "now") if now else None
    store = SQLiteCaseStore(db)
    writer = CaseWriter(
        store,
        context_lookup=lambda m, c: accounts.get((m, c)),
        run_status_lookup=runs.get,
        policy=policy,
        **({"clock": lambda: fixed_now} if fixed_now else {}),
    )
    rejected = []
    decisions = []
    for line_no, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            candidate = candidate_from_dict(json.loads(line))
        except (ContractError, json.JSONDecodeError) as exc:
            rejected.append({"line": line_no, "error": str(exc)})
            continue
        result = writer.handle(candidate)
        decisions.append({
            "event_id": candidate.event_id,
            "outcome": result.decision.outcome.value,
            "reason": result.decision.reason,
            "case_key": result.case.case_key if result.case else None,
            "created": result.created,
            "error": result.error,
        })
    summary = {
        "store": "local",
        "decisions": decisions,
        "rejected_events": rejected,
        "cases": [
            {"case_key": c.case_key, "status": c.status.value, "assigned_to": c.assigned_to,
             "decision_reason": c.decision_reason, "needs_recheck": c.needs_recheck, "store": c.store}
            for c in store.list_cases()
        ],
        "audit_actions": dict(Counter(a["action"] for a in store.list_audit())),
        "exceptions": [{"kind": e["kind"], "event_id": e["event_id"], "message": e["message"]}
                       for e in store.list_exceptions()],
    }
    store.close()
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay events through the local HomeBridge review-case writer.")
    parser.add_argument("--events", type=Path, required=True, help="JSONL file of candidate events")
    parser.add_argument("--context", type=Path, required=True, help="JSON file of account context and run status")
    parser.add_argument("--policy", type=Path, required=True, help="JSON routing policy")
    parser.add_argument("--db", default=":memory:", help="SQLite file for the local case store")
    parser.add_argument("--now", default=None, help="Fixed ISO 8601 clock for a repeatable replay")
    args = parser.parse_args(argv)
    json.dump(replay(args.events, args.context, args.policy, args.db, args.now), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
