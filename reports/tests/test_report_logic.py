"""Tests for the report reference rules in reports/logic/views.sql: INV-5, T03, T04, T05, T07 and segment history."""

from datetime import datetime
from pathlib import Path

import duckdb
import pytest

REPORTS = Path(__file__).resolve().parents[1]
AS_OF = "TIMESTAMP '2026-11-03 10:00:00'"
DAY = 1440


@pytest.fixture
def db():
    con = duckdb.connect()
    con.execute((REPORTS / "logic" / "gold_schema.sql").read_text(encoding="utf-8"))
    con.execute((REPORTS / "tests" / "fixtures" / "gold_fixture.sql").read_text(encoding="utf-8"))
    con.execute((REPORTS / "logic" / "views.sql").read_text(encoding="utf-8"))
    yield con
    con.close()


def rows(con, sql):
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def by(key, items):
    return {item[key]: item for item in items}


class TestScoreAgeAndStaleness:
    def test_failed_latest_run_marks_the_last_good_score_stale(self, db):
        scores = by("mortgage_id", rows(db, f"SELECT * FROM latest_score({AS_OF}, {DAY})"))
        alex = scores["MTG-ALEX"]
        assert alex["run_id"] == "RUN-0002" and alex["score"] == pytest.approx(0.81)
        assert alex["score_status"] == "stale_last_run_failed"
        assert alex["score_age_minutes"] == 30

    def test_partial_rows_from_a_failed_run_are_never_shown(self, db):
        shown = {r["run_id"] for r in rows(db, f"SELECT run_id FROM latest_score({AS_OF}, {DAY})")}
        assert "RUN-0003" not in shown

    def test_score_is_current_before_the_failed_run_starts(self, db):
        scores = by("mortgage_id", rows(db, f"SELECT * FROM latest_score(TIMESTAMP '2026-11-03 09:40:00', {DAY})"))
        assert scores["MTG-ALEX"]["score_status"] == "current"

    def test_old_score_is_stale(self, db):
        db.execute("UPDATE score_runs SET status = 'completed', completed_at = TIMESTAMP '2026-11-03 09:55:00' WHERE run_id = 'RUN-0003'")
        db.execute("DELETE FROM account_scores WHERE run_id = 'RUN-0003'")
        scores = by("mortgage_id", rows(db, f"SELECT * FROM latest_score({AS_OF}, 20)"))
        assert scores["MTG-SAM"]["score_status"] == "stale_too_old"

    def test_account_without_a_score_stays_in_the_view(self, db):
        scores = by("mortgage_id", rows(db, f"SELECT * FROM latest_score({AS_OF}, {DAY})"))
        assert scores["MTG-JO"]["score"] is None
        assert scores["MTG-JO"]["score_status"] == "no_score"


class TestT03PaymentFeed:
    def test_current_feed_gives_arrears_and_counts_reversals(self, db):
        pos = by("mortgage_id", rows(db, f"SELECT * FROM payment_position({AS_OF})"))
        assert pos["MTG-ALEX"]["payment_state"] == "current"
        assert float(pos["MTG-ALEX"]["arrears_amount"]) == pytest.approx(605.40)
        assert float(pos["MTG-SAM"]["arrears_amount"]) == pytest.approx(712.10)
        assert pos["MTG-SAM"]["reversal_count"] == 1

    def test_rows_ingested_after_the_report_time_are_excluded(self, db):
        pos = by("mortgage_id", rows(db, f"SELECT * FROM payment_position(TIMESTAMP '2026-11-03 12:00:00')"))
        assert float(pos["MTG-SAM"]["arrears_amount"]) == pytest.approx(0.0)

    def test_account_with_no_payment_rows_is_not_zero_arrears(self, db):
        pos = by("mortgage_id", rows(db, f"SELECT * FROM payment_position({AS_OF})"))
        assert pos["MTG-JO"]["payment_state"] == "no_records"
        assert pos["MTG-JO"]["arrears_amount"] is None

    def test_stale_flag_from_validation_hides_arrears(self, db):
        db.execute("UPDATE feed_watermark SET is_stale = TRUE")
        pos = rows(db, f"SELECT * FROM payment_position({AS_OF})")
        assert {p["payment_state"] for p in pos} == {"stale"}
        assert all(p["arrears_amount"] is None for p in pos)

    def test_old_watermark_check_is_stale_even_if_flag_is_false(self, db):
        pos = rows(db, "SELECT * FROM payment_position(TIMESTAMP '2026-11-04 10:00:00')")
        assert {p["payment_state"] for p in pos} == {"stale"}

    def test_missing_feed_is_missing_not_zero(self, db):
        db.execute("DELETE FROM feed_watermark")
        pos = rows(db, f"SELECT * FROM payment_position({AS_OF})")
        assert {p["payment_state"] for p in pos} == {"missing"}
        assert all(p["arrears_amount"] is None for p in pos)


class TestT07SupportHistory:
    def test_repeat_contacts_on_one_case_differ_from_separate_cases(self, db):
        cases = by("case_id", rows(db, f"SELECT * FROM support_history({AS_OF}) WHERE mortgage_id = 'MTG-ALEX'"))
        assert cases["C-A1"]["contacts"] == 3
        assert cases["C-A1"]["status"] == "reopened" and not cases["C-A1"]["is_resolved"]
        assert cases["C-A1"]["repeat_contact_unresolved"]
        assert [cases[c]["contacts"] for c in ("C-A2", "C-A3", "C-A4")] == [1, 1, 1]

    def test_state_changes_are_not_counted_as_contacts(self, db):
        case = by("case_id", rows(db, f"SELECT * FROM support_history({AS_OF})"))["C-A1"]
        state_changes = db.execute("SELECT count(*) FROM servicing_case_event WHERE case_id = 'C-A1'").fetchone()[0]
        assert state_changes == 4 and case["contacts"] == 3

    def test_resolution_recorded_after_the_report_time_is_not_used(self, db):
        at_ten = by("case_id", rows(db, f"SELECT * FROM support_history({AS_OF})"))["C-A4"]
        at_noon = by("case_id", rows(db, "SELECT * FROM support_history(TIMESTAMP '2026-11-03 12:00:00')"))["C-A4"]
        assert not at_ten["is_resolved"] and at_noon["is_resolved"]

    def test_summary(self, db):
        alex = by("mortgage_id", rows(db, f"SELECT * FROM support_summary({AS_OF})"))["MTG-ALEX"]
        assert alex["cases"] == 4
        assert alex["contacts"] == 6
        assert alex["unresolved_cases"] == 2
        assert alex["repeat_contacts_on_unresolved"] == 2
        assert alex["max_contacts_on_one_unresolved_case"] == 3
        assert alex["single_contact_cases"] == 3


class TestSegmentHistory:
    @pytest.mark.parametrize("when, expected", [
        ("2026-11-03 08:59:00", None),
        ("2026-11-03 09:15:00", "SEG-1"),
        ("2026-11-03 09:30:00", "SEG-2"),
        ("2026-11-03 10:00:00", "SEG-2"),
    ])
    def test_segment_on_any_past_date(self, db, when, expected):
        found = rows(db, f"SELECT segment_id FROM segment_as_of(TIMESTAMP '{when}') WHERE mortgage_id = 'MTG-ALEX'")
        assert [r["segment_id"] for r in found] == ([expected] if expected else [])

    def test_new_run_appends_rows_and_keeps_past_answers(self, db):
        before = rows(db, "SELECT segment_id FROM segment_as_of(TIMESTAMP '2026-11-03 09:15:00')")
        db.execute("INSERT INTO segment_membership VALUES ('MTG-ALEX|CYC-2027-01', 'MTG-ALEX', 'CYC-2027-01', 'RUN-0004', 'SEG-1', 'clu-0.1', TIMESTAMP '2026-11-03 10:30:00', TIMESTAMP '2026-11-03 10:30:00', NULL)")
        assert rows(db, "SELECT segment_id FROM segment_as_of(TIMESTAMP '2026-11-03 09:15:00')") == before
        now = rows(db, "SELECT segment_id FROM segment_as_of(TIMESTAMP '2026-11-03 10:00:00') WHERE mortgage_id = 'MTG-ALEX'")
        later = rows(db, "SELECT segment_id FROM segment_as_of(TIMESTAMP '2026-11-03 11:00:00') WHERE mortgage_id = 'MTG-ALEX'")
        assert (now, later) == ([{"segment_id": "SEG-2"}], [{"segment_id": "SEG-1"}])
        assert db.execute("SELECT count(*) FROM segment_conflicts()").fetchone()[0] == 0

    def test_conflict_check_finds_two_rows_with_one_start_time(self, db):
        db.execute("INSERT INTO segment_membership VALUES ('MTG-ALEX|CYC-2027-01', 'MTG-ALEX', 'CYC-2027-01', 'RUN-0009', 'SEG-3', 'clu-0.1', TIMESTAMP '2026-11-03 09:30:00', TIMESTAMP '2026-11-03 09:30:00', NULL)")
        assert db.execute("SELECT count(*) FROM segment_conflicts()").fetchone()[0] == 1


class TestRequestStatus:
    def test_days_at_stage_and_portal_faults(self, db):
        req = by("application_id", rows(db, f"SELECT * FROM request_status({AS_OF})"))
        alex = req["APP-ALEX-1"]
        assert alex["current_stage"] == "documents_outstanding"
        assert alex["stage_entered_at"] == datetime(2026, 10, 22, 10, 0)
        assert alex["days_at_stage"] == 12
        assert (alex["portal_errors"], alex["verified_portal_faults"]) == (2, 1)

    def test_rejection_is_not_a_portal_fault(self, db):
        jo = by("application_id", rows(db, f"SELECT * FROM request_status({AS_OF})"))["APP-JO-1"]
        assert jo["portal_errors"] == 0 and jo["verified_portal_faults"] == 0


class TestAccountView:
    def test_alex_row(self, db):
        alex = by("mortgage_id", rows(db, f"SELECT * FROM account_view({AS_OF}, {DAY})"))["MTG-ALEX"]
        assert alex["checkpoint_at"] == datetime(2026, 11, 2)
        assert alex["days_to_deal_end"] == 89
        assert alex["segment_id"] == "SEG-2"
        assert alex["score_status"] == "stale_last_run_failed"
        assert alex["payment_support_priority"] is True
        assert alex["renewal_review_status"] == "on_hold"
        assert alex["review_owner"] == "ADV-011"
        assert alex["verified_portal_faults"] == 1

    def test_every_account_appears_once(self, db):
        view = rows(db, f"SELECT account_key FROM account_view({AS_OF}, {DAY})")
        assert sorted(r["account_key"] for r in view) == [
            "MTG-ALEX|CYC-2027-01", "MTG-JO|CYC-2027-02", "MTG-SAM|CYC-2027-01",
        ]

    def test_account_without_cases_falls_back_to_the_account_owner(self, db):
        jo = by("mortgage_id", rows(db, f"SELECT * FROM account_view({AS_OF}, {DAY})"))["MTG-JO"]
        assert jo["review_owner"] == "ADV-017"
        assert jo["payment_support_priority"] is False
        assert jo["arrears_amount"] is None
