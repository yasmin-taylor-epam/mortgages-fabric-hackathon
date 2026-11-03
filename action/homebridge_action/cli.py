"""Replay candidate events through the local review-case writer.

Two sources of candidates. Use one or both:

- ``--events``: a JSONL file of candidates in the contract shape (``risk_updated`` or service events).
- ``--hbdata-dir``: the Data and replay folder (``data/``). The command reads the local Eventhouse
  stand-in that ``python -m hbdata all`` and ``python -m hbdata replay`` write. It derives service
  events with ``hbdata.replay.service_events``, the Python twin of the KQL function ``ServiceEvents()``.
  It reads the account context from ``output/eventhouse/account_context.parquet``. If the Models and
  evaluation Gold tables exist, it also reads published ``risk_updated`` events from
  ``output/lakehouse/gold/risk_outbox.parquet`` and run statuses from ``score_runs.parquet``.
  This mode needs pandas and pyarrow.

Examples:
    python -m homebridge_action.cli --events tests/fixtures/replay.jsonl \
        --context tests/fixtures/context.json --policy tests/fixtures/policy.json --now 2026-11-03T10:00:00Z

    python -m homebridge_action.cli --hbdata-dir ../data --mortgage HB-M-900001 \
        --policy tests/fixtures/policy.json --now 2026-10-16T12:00:00Z

The output is a JSON summary of cases, audit actions and exceptions. All cases
show store = "local". This run does not write to SharePoint.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

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


def load_hbdata(data_dir: Path, mortgages: Optional[set[str]]
                ) -> tuple[dict[tuple[str, str], AccountContext], list[dict], dict[str, str]]:
    """Read account context and service events from the Data and replay local Eventhouse.

    If the Models and evaluation Gold tables exist, also read published risk_updated payloads from
    risk_outbox and run statuses from score_runs. Candidates come back in arrival order.
    """
    data_dir = data_dir.resolve()
    if str(data_dir) not in sys.path:
        sys.path.insert(0, str(data_dir))
    import pandas as pd
    from hbdata import replay

    eventhouse = data_dir / "output" / "eventhouse"
    context_path = eventhouse / "account_context.parquet"
    if not context_path.exists():
        raise SystemExit(f"{context_path} not found. Run: cd data; python -m hbdata all")
    context = pd.read_parquet(context_path)
    raw, _quarantined = replay.read_local(eventhouse)
    if mortgages:
        context_rows = context[context["mortgage_id"].isin(mortgages)]
        raw = raw[raw["mortgage_id"].isin(mortgages)]
    else:
        context_rows = context
    events = replay.service_events(raw, context)
    timed = [(e["ingested_at"], e["event_id"], e) for e in json.loads(events.to_json(orient="records", date_format="iso"))]

    runs: dict[str, str] = {}
    gold = data_dir / "output" / "lakehouse" / "gold"
    if (gold / "risk_outbox.parquet").exists() and (gold / "score_runs.parquet").exists():
        score_runs = pd.read_parquet(gold / "score_runs.parquet")
        runs = dict(zip(score_runs["run_id"].astype(str), score_runs["status"].astype(str)))
        outbox = pd.read_parquet(gold / "risk_outbox.parquet")
        outbox = outbox[outbox["status"] == "published"]
        if mortgages:
            outbox = outbox[outbox["mortgage_id"].isin(mortgages)]
        for published_at, payload in zip(outbox["published_at"], outbox["payload"]):
            event = json.loads(payload)
            timed.append((pd.Timestamp(published_at).tz_convert("UTC").isoformat(), event["event_id"], event))

    accounts = {}
    for row in json.loads(context_rows.to_json(orient="records", date_format="iso")):
        ctx = AccountContext.from_dict(row)
        accounts[(ctx.mortgage_id, ctx.cycle_id)] = ctx
    ordered = [e for _, _, e in sorted(timed, key=lambda t: (parse_ts(t[0], "arrival time"), t[1]))]
    return accounts, ordered, runs


def read_jsonl(path: Path) -> Iterable[tuple[int, str]]:
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            yield line_no, line


def replay(events_path: Optional[Path], context_path: Optional[Path], policy_path: Path, db: str,
           now: Optional[str], hbdata_dir: Optional[Path] = None, mortgages: Optional[set[str]] = None,
           runs_path: Optional[Path] = None) -> dict[str, Any]:
    accounts: dict[tuple[str, str], AccountContext] = {}
    runs: dict[str, str] = {}
    sourced: list[tuple[str, Any]] = []
    if hbdata_dir:
        hb_accounts, hb_events, hb_runs = load_hbdata(hbdata_dir, mortgages)
        accounts.update(hb_accounts)
        runs.update(hb_runs)
        sourced += [(f"hbdata:{e['event_id']}", e) for e in hb_events]
    if context_path:
        file_accounts, file_runs = load_context(context_path)
        accounts.update(file_accounts)
        runs.update(file_runs)
    if runs_path:
        runs.update({str(k): str(v) for k, v in json.loads(runs_path.read_text(encoding="utf-8")).items()})
    if events_path:
        sourced += [(f"line {n}", line) for n, line in read_jsonl(events_path)]

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
    for source, item in sourced:
        try:
            candidate = candidate_from_dict(json.loads(item) if isinstance(item, str) else item)
        except (ContractError, json.JSONDecodeError, ValueError) as exc:
            rejected.append({"source": source, "error": str(exc)})
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
    parser.add_argument("--events", type=Path, help="JSONL file of candidates in the contract shape")
    parser.add_argument("--context", type=Path, help="JSON file of account context and run status")
    parser.add_argument("--hbdata-dir", type=Path, help="Data and replay folder (data/) with a local Eventhouse")
    parser.add_argument("--mortgage", action="append", help="Limit --hbdata-dir events and context to this mortgage_id")
    parser.add_argument("--runs", type=Path, help="JSON object of run_id to scoring-run status")
    parser.add_argument("--policy", type=Path, required=True, help="JSON routing policy")
    parser.add_argument("--db", default=":memory:", help="SQLite file for the local case store")
    parser.add_argument("--now", default=None, help="Fixed ISO 8601 clock for a repeatable replay")
    args = parser.parse_args(argv)
    if not (args.events or args.hbdata_dir):
        parser.error("give --events, --hbdata-dir or both")
    summary = replay(args.events, args.context, args.policy, args.db, args.now, args.hbdata_dir,
                     set(args.mortgage) if args.mortgage else None, args.runs)
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
