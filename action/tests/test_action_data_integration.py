"""Integration with the Data and replay package (data/).

These tests use the Data and replay contracts, fixtures and the Python twin of ServiceEvents().
They skip when data/hbdata is not present, for example before the Data and replay branch merges.
Set HOMEBRIDGE_DATA_DIR to point at another data/ folder.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from action_helpers import FIXTURES, POLICY
from homebridge_action import CaseWriter, Outcome, SQLiteCaseStore, candidate_from_dict, make_case_key
from homebridge_action.models import AccountContext, ActionFamily, IdentityStatus, PaymentSupportStatus

ACTION_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("HOMEBRIDGE_DATA_DIR", ACTION_DIR.parent / "data")).resolve()
CASES = DATA_DIR / "fixtures" / "cases"

pytestmark = pytest.mark.skipif(not (DATA_DIR / "hbdata").is_dir(), reason=f"Data and replay package not found at {DATA_DIR}")


@pytest.fixture(scope="module")
def hb():
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    if str(DATA_DIR) not in sys.path:
        sys.path.insert(0, str(DATA_DIR))
    from hbdata import contracts, replay

    return pd, contracts, replay


def load_case(name):
    return json.loads((CASES / f"{name}.json").read_text(encoding="utf-8"))


def context_from_accounts(pd, rows, support="none"):
    frame = pd.DataFrame([{
        "mortgage_id": r["mortgage_id"], "cycle_id": r["cycle_id"],
        "deal_start_at": pd.Timestamp(r["deal_start_at"]), "deal_end_at": pd.Timestamp(r["deal_end_at"]),
        "identity_status": "ok", "payment_support_status": support, "owner_id": r["owner_id"],
        "context_version": "ctx-fixture",
    } for r in rows])
    lookup = {
        (r["mortgage_id"], r["cycle_id"]): AccountContext(
            r["mortgage_id"], r["cycle_id"], IdentityStatus.OK, PaymentSupportStatus(support), r["owner_id"], "ctx-fixture")
        for r in rows
    }
    return frame, lookup


def service_event_records(hb, tmp_path, envelopes, context_frame):
    pd, contracts, replay = hb
    replay.ingest_local(envelopes, tmp_path, context_frame, now=pd.Timestamp("2026-10-20T12:00:00Z"))
    raw, _ = replay.read_local(tmp_path)
    records = json.loads(replay.service_events(raw, context_frame).to_json(orient="records", date_format="iso"))
    assert contracts.validate_records(records, "service_event") == []
    return sorted(records, key=lambda r: (r["ingested_at"], r["event_id"]))


def writer_for(store, lookup, now, runs=None):
    return CaseWriter(store, context_lookup=lambda m, c: lookup.get((m, c)),
                      run_status_lookup=(runs or {}).get, policy=POLICY, clock=lambda: now)


def test_t01_replayed_portal_fault_gives_one_technical_case(hb, tmp_path):
    case = load_case("T01")
    frame, lookup = context_from_accounts(hb[0], case["inputs"]["mortgage_account"])
    records = service_event_records(hb, tmp_path, case["live_events"], frame)
    store = SQLiteCaseStore()
    writer = writer_for(store, lookup, datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
    # Activator can deliver the same service event more than once. Deliver every record twice.
    for record in records + records:
        writer.handle(candidate_from_dict(record))
    counts = {}
    for c in store.list_cases():
        counts[c.case_key] = counts.get(c.case_key, 0) + 1
    assert counts == case["expected"]["dashboard_review_cases_for_key"]


def test_t04_live_reversal_requests_rescoring_and_flags_the_open_review(hb, tmp_path):
    case = load_case("T04")
    frame, lookup = context_from_accounts(hb[0], case["inputs"]["mortgage_account"])
    records = service_event_records(hb, tmp_path, case["live_events"], frame)
    expected = case["expected"]["service_events"][0]
    assert [(r["event_id"], r["event_type"], r["evidence_ids"]) for r in records] == [
        (expected["event_id"], expected["event_type"], expected["evidence_ids"])
    ]
    account = case["inputs"]["mortgage_account"][0]
    store = SQLiteCaseStore()
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    writer = writer_for(store, lookup, now, {"RUN-T04": "completed"})
    risk = {
        "event_id": "RUP-T04", "event_type": "risk_updated", "schema_version": "1.0",
        "mortgage_id": account["mortgage_id"], "cycle_id": account["cycle_id"], "run_id": "RUN-T04",
        "snapshot_id": "SNP-00000000000000a4", "model_version": "hb-refi-clf:1", "cluster_model_version": "hb-seg:1",
        "segment_id": "S1", "scored_at": "2026-10-20T08:03:00Z", "cutoff_at": "2026-10-20T08:00:00Z",
        "target": "confirmed_external_refinancing", "score": 0.9, "evidence_ids": ["PTR-T04"],
    }
    assert hb[1].validate_records([risk], "risk_updated") == []
    writer.handle(candidate_from_dict(risk))
    audit_before = store.list_audit()

    result = writer.handle(candidate_from_dict(records[0]))

    renewal_key = make_case_key(account["mortgage_id"], account["cycle_id"], ActionFamily.RENEWAL)
    assert result.decision.outcome == Outcome.RECHECK
    assert store.get_case(renewal_key).needs_recheck is True
    assert len(store.list_cases()) == 1
    audit = store.list_audit()
    assert audit[: len(audit_before)] == audit_before
    decision = next(a for a in audit if a["event_id"] == expected["event_id"] and a["action"] == "decision")
    assert decision["details"]["evidence_ids"] == expected["evidence_ids"]
    assert any(a["action"] == "rescore_requested" for a in audit)


class TimeoutThenCommitted(SQLiteCaseStore):
    """T06 simulated failures: attempt 1 times out after the row was written; attempt 2 meets the duplicate key."""

    def __init__(self, failures):
        super().__init__()
        self.failures = {f["attempt"]: f["result"] for f in failures}
        self.attempts = []

    def insert_case(self, case):
        from homebridge_action import TransientWriteError

        self.attempts.append(case.source_event_id)
        result = self.failures.get(len(self.attempts))
        super().insert_case(case)
        if result == "timeout":
            raise TransientWriteError("simulated timeout")


def test_t06_write_retries_with_the_same_key_and_creates_one_case(hb):
    case = load_case("T06")
    event = case["inputs"]["risk_updated"][0]
    assert hb[1].validate_records([event], "risk_updated") == []
    expected = case["expected"]
    store = TimeoutThenCommitted(case["simulated_failures"])
    lookup = {(event["mortgage_id"], event["cycle_id"]): AccountContext(
        event["mortgage_id"], event["cycle_id"], IdentityStatus.OK, PaymentSupportStatus.NONE, "ADV-001", "ctx-fixture")}
    now = datetime(2026, 10, 20, 8, 10, tzinfo=timezone.utc)
    writer = writer_for(store, lookup, now, {event["run_id"]: "completed"})

    result = writer.handle(candidate_from_dict(event))

    assert result.case.case_key == expected["case_key"]
    assert [c.case_key for c in store.list_cases()].count(expected["case_key"]) == expected["review_cases_for_key"]
    assert set(store.attempts) == {expected["event_id_on_every_attempt"]} and len(store.attempts) == 2
    assert {e["event_id"] for e in store.list_exceptions()} == {expected["event_id_on_every_attempt"]}


def test_demo_alex_replay_routes_payment_support_first(hb, tmp_path):
    pd, _, replay = hb
    eventhouse = DATA_DIR / "output" / "eventhouse"
    if not (eventhouse / "account_context.parquet").exists():
        pytest.skip("run: cd data; python -m hbdata all")
    context = pd.read_parquet(eventhouse / "account_context.parquet")
    alex = context[context["mortgage_id"] == "HB-M-900001"]
    assert len(alex) >= 1
    envelopes = []
    for name in ("demo_alex.jsonl", "demo_alex_duplicate.jsonl"):
        envelopes += replay.read_jsonl(DATA_DIR / "fixtures" / "replay" / name)
    records = service_event_records(hb, tmp_path, envelopes, alex)
    assert [r["event_type"] for r in records] == ["portal_fault_verified", "payment_support_requested"]
    lookup = {}
    for row in json.loads(alex.to_json(orient="records", date_format="iso")):
        ctx = AccountContext.from_dict(row)
        lookup[(ctx.mortgage_id, ctx.cycle_id)] = ctx
    store = SQLiteCaseStore()
    writer = writer_for(store, lookup, datetime(2026, 10, 16, 12, tzinfo=timezone.utc))
    for record in records + records[:1]:
        writer.handle(candidate_from_dict(record))
    cycle = records[0]["cycle_id"]
    assert sorted(c.case_key for c in store.list_cases()) == [
        f"HB-M-900001|{cycle}|support", f"HB-M-900001|{cycle}|technical",
    ]
    support = store.get_case(f"HB-M-900001|{cycle}|support")
    assert support.assigned_to == POLICY.support_queue
    assert support.evidence_ids == ("PSE-900000003", "CASE-9000003")


def test_cli_reads_the_local_eventhouse(tmp_path):
    pytest.importorskip("pandas")
    if not (DATA_DIR / "output" / "eventhouse" / "account_context.parquet").exists():
        pytest.skip("run: cd data; python -m hbdata all")
    out = subprocess.run(
        [sys.executable, "-m", "homebridge_action.cli", "--hbdata-dir", str(DATA_DIR), "--mortgage", "HB-M-900001",
         "--policy", str(FIXTURES / "policy.json"), "--now", "2026-10-16T12:00:00Z"],
        cwd=ACTION_DIR, capture_output=True, text=True, check=True,
    )
    summary = json.loads(out.stdout)
    assert summary["rejected_events"] == []
    assert all(c["store"] == "local" for c in summary["cases"])
    assert all(c["case_key"].startswith("HB-M-900001|") for c in summary["cases"])
