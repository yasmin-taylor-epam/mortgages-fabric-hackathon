"""Tests for the notebook 07 routing helper. Synthetic data only."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[3]
for p in (REPO / "notebooks", REPO / "action", REPO / "data"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from homebridge_action.models import RoutingPolicy  # noqa: E402
from review_routing import route as rr  # noqa: E402

POLICY = RoutingPolicy.from_dict(json.loads((REPO / "action" / "policy" / "routing-policy.candidate.json").read_text("utf-8")))


def ctx(mortgage_id, support="none", owner="ADV-001"):
    return {"mortgage_id": mortgage_id, "cycle_id": "C01", "identity_status": "ok",
            "payment_support_status": support, "owner_id": owner, "context_version": "ctx-test"}


def risk_row(mortgage_id, score, rank, run_id="RUN-B", scored_at="2026-11-09T15:25:00Z"):
    payload = {"event_id": f"RU-{mortgage_id}-{run_id}", "event_type": "risk_updated", "mortgage_id": mortgage_id,
               "cycle_id": "C01", "run_id": run_id, "snapshot_id": "SNP-1", "model_version": "m:1",
               "cluster_model_version": "c:1", "segment_id": "SEG-1", "scored_at": scored_at,
               "cutoff_at": "2026-10-15T00:00:00Z", "target": "refinance_away", "score": score,
               "evidence_ids": ["SNP-1"], "priority_rank": rank}
    return {"event_id": payload["event_id"], "run_id": run_id, "mortgage_id": mortgage_id, "cycle_id": "C01",
            "payload": json.dumps(payload), "status": "published", "published_at": pd.Timestamp(scored_at)}


def support_event(mortgage_id, ingested_at="2026-11-10T09:00:00Z"):
    return {"event_id": f"EV-{mortgage_id}", "event_type": "payment_support_requested", "mortgage_id": mortgage_id,
            "cycle_id": "C01", "occurred_at": "2026-10-16T09:00:00Z", "ingested_at": ingested_at,
            "evidence_ids": json.dumps([f"EV-{mortgage_id}", "CASE-1"])}


RUNS = pd.DataFrame([
    {"run_id": "RUN-A", "status": "completed", "completed_at": pd.Timestamp("2026-10-15T00:05:00Z")},
    {"run_id": "RUN-B", "status": "completed", "completed_at": pd.Timestamp("2026-11-09T15:25:00Z")},
    {"run_id": "RUN-C", "status": "failed", "completed_at": pd.Timestamp("2026-11-09T16:00:00Z")},
])


def run(outbox_rows, service_rows, contexts):
    run_id = rr.latest_completed_run(RUNS)
    cands = rr.risk_candidates(pd.DataFrame(outbox_rows), run_id) + rr.service_candidates(service_rows)
    accounts = rr.contexts(pd.DataFrame(contexts))
    runs = dict(zip(RUNS["run_id"], RUNS["status"]))
    return rr.route(accounts, cands, runs, POLICY, "2026-11-10T10:00:00Z")


def test_latest_completed_run_ignores_failed_runs():
    assert rr.latest_completed_run(RUNS) == "RUN-B"


def test_only_the_latest_run_creates_renewal_cases_with_its_rank():
    out = run([risk_row("M1", 0.9, 1, run_id="RUN-A", scored_at="2026-10-15T00:05:00Z"), risk_row("M1", 0.8, 2),
               risk_row("M2", 0.2, 3)], [], [ctx("M1"), ctx("M2")])
    cases = out["cases"]
    assert list(cases["case_key"]) == ["M1|C01|renewal"]
    assert cases.iloc[0]["priority_rank"] == 2 and cases.iloc[0]["assigned_to"] == "ADV-001"
    assert cases.iloc[0]["store"] == "fabric" and cases.iloc[0]["status"] == "open"
    assert set(out["decisions"]["reason"]) == {"eligible_refinancing_risk", "below_threshold"}


def test_payment_help_request_creates_a_support_case_and_holds_renewal():
    out = run([risk_row("M1", 0.9, 1)], [support_event("M1")], [ctx("M1")])
    by_family = out["cases"].set_index("action_family")
    assert by_family.loc["support", "assigned_to"] == "payment-support-team"
    assert by_family.loc["renewal", "status"] == "on_hold"
    assert by_family.loc["support", "case_created_at"] == pd.Timestamp("2026-10-16T09:00:00Z")  # the event time


def test_rerun_gives_the_same_cases():
    args = ([risk_row("M1", 0.9, 1)], [support_event("M1")], [ctx("M1")])
    a, b = run(*args)["cases"], run(*args)["cases"]
    pd.testing.assert_frame_equal(a.drop(columns="routed_at"), b.drop(columns="routed_at"))


def test_duplicate_event_does_not_duplicate_the_case():
    ev = support_event("M1")
    out = run([], [ev, dict(ev)], [ctx("M1")])
    assert len(out["cases"]) == 1


def test_unknown_account_is_not_routed_to_a_person():
    out = run([], [support_event("M9")], [ctx("M1")])
    assert out["cases"].empty
    assert out["decisions"].iloc[0]["outcome"] == "quarantine"


OUTPUT = REPO / "data" / "output"


@pytest.mark.skipif(not (OUTPUT / "lakehouse" / "gold" / "risk_outbox.parquet").exists(),
                    reason="needs the local flow output: scripts/run_local_flow.ps1")
def test_local_flow_output_matches_the_action_cli():
    from hbdata import replay
    from homebridge_action import cli

    eventhouse, gold = OUTPUT / "eventhouse", OUTPUT / "lakehouse" / "gold"
    context = pd.read_parquet(eventhouse / "account_context.parquet")
    raw, _ = replay.read_local(eventhouse)
    service = json.loads(replay.service_events(raw, context).to_json(orient="records", date_format="iso"))
    score_runs = pd.read_parquet(gold / "score_runs.parquet")
    run_id = rr.latest_completed_run(score_runs)
    cands = rr.risk_candidates(pd.read_parquet(gold / "risk_outbox.parquet"), run_id) + rr.service_candidates(service)
    out = rr.route(rr.contexts(context), cands, dict(zip(score_runs["run_id"], score_runs["status"])), POLICY,
                   "2026-10-15T12:00:00Z")
    expected = cli.replay(None, None, REPO / "action" / "policy" / "routing-policy.candidate.json", ":memory:",
                          "2026-10-15T12:00:00Z", REPO / "data")
    assert set(out["cases"]["case_key"]) == {c["case_key"] for c in expected["cases"]}
    renewal = out["cases"][out["cases"]["action_family"] == "renewal"]
    assert renewal["priority_rank"].notna().all()
