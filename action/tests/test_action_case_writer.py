"""Case-writer tests: INV-3, T01, T04, T05, T06 and the audit record."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from action_helpers import FIXTURES, NOW, Harness, make_risk, make_service
from homebridge_action import CaseStatus, Outcome, SQLiteCaseStore, TransientWriteError, make_case_key
from homebridge_action.models import ActionFamily

ACTION_DIR = Path(__file__).resolve().parents[1]
RENEWAL_KEY = make_case_key("MTG-1", "CYC-1", ActionFamily.RENEWAL)
SUPPORT_KEY = make_case_key("MTG-1", "CYC-1", ActionFamily.SUPPORT)


class FlakyStore(SQLiteCaseStore):
    """Fails the first `failures` inserts. With commit_then_fail, the row is written before the error."""

    def __init__(self, failures, commit_then_fail=False):
        super().__init__()
        self.failures = failures
        self.commit_then_fail = commit_then_fail
        self.insert_calls = 0

    def insert_case(self, case):
        self.insert_calls += 1
        if self.insert_calls <= self.failures:
            if self.commit_then_fail:
                super().insert_case(case)
            raise TransientWriteError("simulated connector timeout")
        super().insert_case(case)


def actions(store):
    return [a["action"] for a in store.list_audit()]


class TestT01AndInv3OneCasePerKey:
    def test_replayed_event_creates_one_case(self, harness):
        first = harness.writer.handle(make_risk())
        second = harness.writer.handle(make_risk())
        assert first.created and not second.created
        assert second.case.case_key == first.case.case_key == RENEWAL_KEY
        assert len(harness.store.list_cases()) == 1
        assert "duplicate_key_read_existing" in actions(harness.store)

    def test_different_events_for_the_same_key_share_one_case(self, harness):
        harness.writer.handle(make_risk(event_id="EVT-A"))
        result = harness.writer.handle(make_risk(event_id="EVT-B", score=0.95))
        assert not result.created
        assert result.case.source_event_id == "EVT-A"
        assert len(harness.store.list_cases()) == 1

    def test_store_rejects_a_second_row_for_a_key(self, harness):
        harness.writer.handle(make_risk())
        with pytest.raises(sqlite3.IntegrityError):
            harness.store.conn.execute(
                "INSERT INTO review_cases SELECT * FROM review_cases WHERE case_key = ?", (RENEWAL_KEY,)
            )

    def test_families_are_separate_keys(self, harness):
        harness.writer.handle(make_service("portal_fault_verified", event_id="EVT-F"))
        harness.writer.handle(make_risk())
        keys = {c.case_key for c in harness.store.list_cases()}
        assert keys == {RENEWAL_KEY, make_case_key("MTG-1", "CYC-1", ActionFamily.TECHNICAL)}


class TestT06WriteFailure:
    def test_transient_failure_retries_with_the_same_key(self):
        h = Harness(store=FlakyStore(failures=2))
        result = h.writer.handle(make_risk())
        assert result.created and result.case.case_key == RENEWAL_KEY
        assert len(h.store.list_cases()) == 1
        failures = [e for e in h.store.list_exceptions() if e["kind"] == "case_write_failed"]
        assert [e["attempt"] for e in failures] == [1, 2]
        assert {e["case_key"] for e in failures} == {RENEWAL_KEY}

    def test_write_that_committed_before_the_error_does_not_duplicate(self):
        h = Harness(store=FlakyStore(failures=1, commit_then_fail=True))
        result = h.writer.handle(make_risk())
        assert not result.created
        assert result.case is not None and result.case.case_key == RENEWAL_KEY
        assert len(h.store.list_cases()) == 1
        assert "duplicate_key_read_existing" in actions(h.store)

    def test_exhausted_retries_stay_visible_and_create_nothing(self):
        h = Harness(store=FlakyStore(failures=10), max_attempts=3)
        result = h.writer.handle(make_risk())
        assert result.case is None and result.error
        assert h.store.list_cases() == []
        kinds = [e["kind"] for e in h.store.list_exceptions()]
        assert kinds.count("case_write_failed") == 3
        assert "case_write_retries_exhausted" in kinds
        assert "case_write_failed" in actions(h.store)

    def test_retry_rechecks_payment_support_on_redelivery(self, harness):
        harness.set_support("unknown")
        held = harness.writer.handle(make_risk())
        assert held.decision.outcome == Outcome.HOLD
        harness.set_support("active")
        redelivered = harness.writer.handle(make_risk())
        assert redelivered.case.action_family == ActionFamily.SUPPORT
        assert harness.store.get_case(RENEWAL_KEY) is None


class TestPaymentSupportHold:
    def test_support_request_holds_an_open_renewal_case_and_keeps_it(self, harness):
        harness.writer.handle(make_risk())
        audit_before = harness.store.list_audit()
        harness.writer.handle(make_service("payment_support_requested", event_id="EVT-SUP"))
        renewal = harness.store.get_case(RENEWAL_KEY)
        assert renewal is not None and renewal.status == CaseStatus.ON_HOLD
        assert harness.store.get_case(SUPPORT_KEY).status == CaseStatus.OPEN
        assert harness.store.list_audit()[: len(audit_before)] == audit_before
        assert "case_held" in actions(harness.store)

    def test_risk_event_during_active_support_creates_no_renewal_case(self, harness):
        harness.set_support("active")
        result = harness.writer.handle(make_risk(score=0.99))
        assert result.case.action_family == ActionFamily.SUPPORT
        assert harness.store.get_case(RENEWAL_KEY) is None


class TestT04Reversal:
    def test_reversal_flags_review_requests_rescoring_and_keeps_audit(self, harness):
        harness.writer.handle(make_risk())
        audit_before = harness.store.list_audit()
        result = harness.writer.handle(make_service("payment_reversed", event_id="EVT-REV"))
        assert result.decision.outcome == Outcome.RECHECK
        assert harness.store.get_case(RENEWAL_KEY).needs_recheck is True
        assert len(harness.store.list_cases()) == 1
        assert harness.store.list_audit()[: len(audit_before)] == audit_before
        assert {"case_flagged_recheck", "rescore_requested"} <= set(actions(harness.store))


class TestT05FailedRun:
    def test_failed_run_event_changes_no_case(self, harness):
        harness.writer.handle(make_risk())
        before = harness.store.get_case(RENEWAL_KEY)
        result = harness.writer.handle(make_risk(event_id="EVT-LATE", run_id="RUN-FAILED", score=0.99))
        assert result.decision.outcome == Outcome.NO_CASE
        assert harness.store.get_case(RENEWAL_KEY) == before

    def test_failed_run_does_not_block_service_routing(self, harness):
        harness.writer.handle(make_risk(run_id="RUN-FAILED"))
        result = harness.writer.handle(make_service("portal_fault_verified"))
        assert result.created


class TestAuditRecord:
    def test_score_based_case_records_model_run_snapshot_and_times(self, harness):
        case = harness.writer.handle(make_risk(evidence=("EV-1", "EV-2"))).case
        assert case.evidence_ids == ("EV-1", "EV-2")
        assert (case.model_version, case.cluster_model_version) == ("clf-test", "clu-test")
        assert (case.run_id, case.snapshot_id) == ("RUN-OK", "SNP-1")
        assert case.routing_policy == "test-policy" and case.assigned_to == "ADV-1"
        assert case.ingested_at and case.scored_at and case.alert_received_at and case.case_created_at
        assert case.store == "local"
        decision = harness.store.list_audit()[0]
        assert decision["action"] == "decision"
        assert decision["details"]["customer_contact"] == "not_authorised"
        assert decision["details"]["run_id"] == "RUN-OK"

    def test_service_case_needs_no_scoring_reference(self, harness):
        case = harness.writer.handle(make_service("portal_fault_verified")).case
        assert case.model_version is None and case.run_id is None
        assert case.evidence_ids == ("EV-S-1",)

    def test_every_decision_is_audited_even_without_a_case(self, harness):
        harness.writer.handle(make_risk(score=0.1))
        audit = harness.store.list_audit()
        assert [a["reason"] for a in audit] == ["below_threshold"]

    @pytest.mark.parametrize("statement", ["UPDATE audit_log SET reason = 'x'", "DELETE FROM audit_log"])
    def test_audit_log_is_append_only(self, harness, statement):
        harness.writer.handle(make_risk())
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            harness.store.conn.execute(statement)

    def test_quarantine_and_hold_are_visible_in_the_exception_register(self, harness):
        harness.writer.handle(make_risk(mortgage_id="MTG-NOBODY"))
        harness.set_support("unknown")
        harness.writer.handle(make_risk())
        kinds = [e["kind"] for e in harness.store.list_exceptions()]
        assert kinds == ["quarantine", "renewal_review_held"]


class TestCliReplay:
    def run_cli(self, db):
        out = subprocess.run(
            [sys.executable, "-m", "homebridge_action.cli",
             "--events", str(FIXTURES / "replay.jsonl"), "--context", str(FIXTURES / "context.json"),
             "--policy", str(FIXTURES / "policy.json"), "--db", str(db), "--now", NOW.isoformat()],
            cwd=ACTION_DIR, capture_output=True, text=True, check=True,
        )
        return json.loads(out.stdout)

    def test_demo_replay_gives_the_expected_cases(self, tmp_path):
        summary = self.run_cli(tmp_path / "cases.sqlite")
        cases = {c["case_key"]: c for c in summary["cases"]}
        assert set(cases) == {
            "MTG-ALEX|CYC-2027-01|support",
            "MTG-ALEX|CYC-2027-01|technical",
            "MTG-JO|CYC-2027-02|renewal",
        }
        assert all(c["store"] == "local" for c in summary["cases"])
        assert len(summary["rejected_events"]) == 1
        reasons = {d["event_id"]: d["reason"] for d in summary["decisions"]}
        assert reasons["EVT-R-SAM-1"] == "below_threshold"
        assert reasons["EVT-R-JO-FAILED"] == "run_not_completed:failed"
        assert reasons["EVT-R-KIM-1"] == "payment_support_status_unknown"
        assert reasons["EVT-R-UNKNOWN-1"] == "unknown_identity"
        assert reasons["EVT-R-LEE-1"] == "conflicting_identity"
        assert summary["audit_actions"]["rescore_requested"] == 1

    def test_second_replay_into_the_same_store_adds_no_cases(self, tmp_path):
        db = tmp_path / "cases.sqlite"
        first = self.run_cli(db)
        second = self.run_cli(db)
        assert first["cases"] == second["cases"]
        assert not any(d["created"] for d in second["decisions"])
