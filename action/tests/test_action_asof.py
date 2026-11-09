"""One as-of date: HB_AS_OF sets the default clock of the case writer. Offline. Synthetic data."""
from datetime import datetime, timedelta, timezone

import pytest
from action_helpers import NOW, POLICY, make_context, make_risk
from homebridge_action import CaseWriter, Outcome, SQLiteCaseStore, clock

SCORED = datetime(2026, 10, 15, 0, 5, tzinfo=timezone.utc)


def default_clock_writer():
    store = SQLiteCaseStore()
    writer = CaseWriter(store, context_lookup=lambda m, c: make_context(m, c), run_status_lookup=lambda r: "completed", policy=POLICY)
    return store, writer


def test_the_as_of_clock_is_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("HB_AS_OF", raising=False)
    assert clock.as_of() is None and abs((clock.now() - datetime.now(timezone.utc)).total_seconds()) < 5
    monkeypatch.setenv("HB_AS_OF", "2026-10-16T09:00:00Z")
    assert clock.as_of() == datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc) == clock.now()
    monkeypatch.setenv("HB_AS_OF", "2026-10-16T10:00:00+01:00")
    assert clock.now() == datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("HB_AS_OF", "not a date")
    with pytest.raises(ValueError, match="HB_AS_OF"):
        clock.as_of()


def test_a_replay_long_after_scoring_gives_no_case_on_the_real_clock_but_a_case_on_the_as_of_clock(monkeypatch):
    event = make_risk(event_id="E-1", scored_at=SCORED, score=0.9)
    monkeypatch.delenv("HB_AS_OF", raising=False)
    store, writer = default_clock_writer()
    assert writer.handle(event).decision.reason == "stale_score"  # the real clock is days later than the frozen data
    store.close()
    monkeypatch.setenv("HB_AS_OF", (SCORED + timedelta(minutes=10)).isoformat())
    store, writer = default_clock_writer()
    result = writer.handle(event)
    assert result.decision.outcome == Outcome.CREATE_CASE
    assert result.case.case_created_at == SCORED + timedelta(minutes=10)
    store.close()


def test_the_case_writer_default_stays_utc_now(monkeypatch):
    from homebridge_action.case_writer import utc_now

    monkeypatch.setenv("HB_AS_OF", "2026-10-16T09:00:00Z")
    assert utc_now() == datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc) and NOW.tzinfo is not None
