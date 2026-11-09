"""Priority rank in the action package: every account at or above the threshold gets a case. The rank only orders the queue.

Offline. Synthetic data.
"""
import sqlite3
import sys
from pathlib import Path

import pytest
from action_helpers import NOW, POLICY, make_context, make_risk
from homebridge_action import CaseWriter, Outcome, SQLiteCaseStore, candidate_from_dict
from homebridge_action.router import route


def run_of(n: int, run_id: str = "RUN-A"):
    return [make_risk(event_id=f"EVT-{run_id}-{i}", mortgage_id=f"MTG-{i}", cycle_id="CYC-1", score=round(0.99 - i * 0.01, 4),
                      run_id=run_id, priority_rank=i) for i in range(1, n + 1)]


def writer(store=None, runs=("RUN-A", "RUN-B")):
    store = store or SQLiteCaseStore()
    contexts = {(f"MTG-{i}", "CYC-1"): make_context(f"MTG-{i}", "CYC-1") for i in range(1, 60)}
    return store, CaseWriter(store, context_lookup=lambda m, c: contexts.get((m, c)), run_status_lookup=lambda r: "completed" if r in runs else None,
                             policy=POLICY, clock=lambda: NOW)


def test_twenty_two_above_the_threshold_open_twenty_two_cases_ordered_by_rank():
    store, w = writer()
    results = [w.handle(e) for e in run_of(22)]
    assert all(r.decision.outcome == Outcome.CREATE_CASE and r.decision.reason == "eligible_refinancing_risk" for r in results)
    cases = sorted(store.list_cases(), key=lambda c: c.priority_rank)
    assert len(cases) == 22  # no cap: the 11th to the 22nd account get cases too
    assert [c.priority_rank for c in cases] == list(range(1, 23))
    assert [c.mortgage_id for c in cases] == [f"MTG-{i}" for i in range(1, 23)]  # the queue order is the score order
    store.close()


def test_the_rank_never_decides_whether_a_case_exists():
    low = make_risk(event_id="E-low", mortgage_id="MTG-1", score=0.61, priority_rank=500)
    assert route(low, make_context(), POLICY, NOW, "completed").outcome == Outcome.CREATE_CASE
    below = make_risk(event_id="E-below", mortgage_id="MTG-1", score=0.2, priority_rank=1)
    assert route(below, make_context(), POLICY, NOW, "completed").reason == "below_threshold"  # the threshold still decides


def test_events_without_a_rank_still_open_a_case_with_no_rank():
    old = {"event_id": "E1", "event_type": "risk_updated", "schema_version": "1.0", "mortgage_id": "MTG-1", "cycle_id": "CYC-1", "run_id": "RUN-A",
           "snapshot_id": "SNP-1", "model_version": "m", "cluster_model_version": "c", "segment_id": "SEG-1",
           "scored_at": "2026-11-03T09:30:00Z", "cutoff_at": "2026-11-03T09:25:00Z", "target": "t", "score": 0.9, "evidence_ids": ["EV-1"]}
    cand = candidate_from_dict(old)
    assert cand.priority_rank is None
    store, w = writer()
    assert w.handle(cand).created and store.list_cases()[0].priority_rank is None
    assert candidate_from_dict({**old, "priority_rank": 4}).priority_rank == 4
    store.close()


def test_replaying_the_same_events_does_not_duplicate_cases():
    store, w = writer()
    events = run_of(22)
    for e in events:
        w.handle(e)
    first = sorted(c.case_key for c in store.list_cases())
    assert not any(w.handle(e).created for e in events)  # at-least-once delivery
    assert sorted(c.case_key for c in store.list_cases()) == first and len(first) == 22
    store.close()


def test_a_second_run_does_not_add_or_change_cases_for_the_same_accounts():
    store, w = writer()
    for e in run_of(22, "RUN-A"):
        w.handle(e)
    before = {c.case_key: c.priority_rank for c in store.list_cases()}
    for e in run_of(22, "RUN-B"):  # a new run, new event ids, the same accounts
        w.handle(e)
    assert {c.case_key: c.priority_rank for c in store.list_cases()} == before and len(before) == 22


def test_a_support_case_from_a_risk_event_is_not_in_the_renewal_queue():
    store, w = writer()
    store.close()
    store = SQLiteCaseStore()
    contexts = {("MTG-1", "CYC-1"): make_context(support="active")}
    w = CaseWriter(store, context_lookup=lambda m, c: contexts.get((m, c)), run_status_lookup=lambda r: "completed", policy=POLICY, clock=lambda: NOW)
    r = w.handle(make_risk(priority_rank=1))
    assert r.case.action_family.value == "support" and r.case.priority_rank is None
    store.close()


def test_a_case_file_made_before_the_rank_existed_still_opens(tmp_path):
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE review_cases (case_key TEXT PRIMARY KEY, mortgage_id TEXT NOT NULL, cycle_id TEXT NOT NULL, action_family TEXT NOT NULL, "
                       "status TEXT NOT NULL, assigned_to TEXT NOT NULL, routing_policy TEXT NOT NULL, decision_reason TEXT NOT NULL, source_event_id TEXT NOT NULL, "
                       "evidence_ids TEXT NOT NULL, alert_received_at TEXT NOT NULL, case_created_at TEXT NOT NULL, ingested_at TEXT, scored_at TEXT, "
                       "model_version TEXT, cluster_model_version TEXT, run_id TEXT, snapshot_id TEXT, needs_recheck INTEGER NOT NULL DEFAULT 0, "
                       "store TEXT NOT NULL DEFAULT 'local');")
    conn.close()
    store, w = writer(SQLiteCaseStore(str(db)))
    assert w.handle(run_of(1)[0]).created and store.list_cases()[0].priority_rank == 1
    store.close()


def test_scoring_output_drives_the_queue_end_to_end():
    """22 scored accounts -> scoring package ranks them -> events -> router -> 22 cases in score order."""
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    pytest.importorskip("sklearn")
    root = Path(__file__).resolve().parents[2]
    for p in (root / "notebooks", root / "data"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from models_eval import scoring

    frame = pd.DataFrame({"mortgage_id": [f"MTG-{i}" for i in range(1, 23)], "cycle_id": "CYC-1", "score": [round(0.99 - i * 0.01, 4) for i in range(1, 23)]})
    store, w = writer()
    for run_id in ("RUN-A", "RUN-B"):
        for r in scoring.apply_priority_rank(frame, POLICY.risk_threshold).itertuples(index=False):
            w.handle(make_risk(event_id=f"EVT-{run_id}-{r.mortgage_id}", mortgage_id=r.mortgage_id, score=r.score, run_id=run_id, priority_rank=int(r.priority_rank)))
    cases = sorted(store.list_cases(), key=lambda c: c.priority_rank)
    assert [c.mortgage_id for c in cases] == [f"MTG-{i}" for i in range(1, 23)]
    store.close()
