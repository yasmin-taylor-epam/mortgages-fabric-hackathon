import pandas as pd

from hbdata import io, journeys

T = pd.Timestamp("2026-10-01T09:00:00Z")


def ev(eid, app, minutes, stage, status, cls=None, verified=None, ingest_delay=1, mid="HB-M-000001"):
    t = T + pd.Timedelta(minutes=minutes)
    return {"event_id": eid, "application_id": app, "mortgage_id": mid, "occurred_at": t,
            "ingested_at": t + pd.Timedelta(minutes=ingest_delay), "stage": stage, "status": status,
            "error_code": "SYS-503" if cls == "software_fault" else None, "error_class": cls,
            "fault_verified": verified, "channel": "portal"}


def frame(rows):
    return io.coerce(pd.DataFrame(rows), "transfer_request_event")


EVENTS = frame([
    ev("PTE-000000001", "PTR-0000001", 0, "started", "in_progress"),
    ev("PTE-000000002", "PTR-0000001", 10, "product_selected", "in_progress"),
    ev("PTE-000000003", "PTR-0000001", 20, "product_selected", "error", "software_fault", True),
    ev("PTE-000000004", "PTR-0000001", 30, "product_selected", "in_progress"),
    ev("PTE-000000005", "PTR-0000001", 40, "product_selected", "error", "software_fault", False),
    ev("PTE-000000006", "PTR-0000001", 50, "product_selected", "in_progress"),
    ev("PTE-000000007", "PTR-0000001", 60 * 24, "documents_outstanding", "in_progress"),
    ev("PTE-000000011", "PTR-0000002", 0, "started", "in_progress", mid="HB-M-000002"),
    ev("PTE-000000012", "PTR-0000002", 5, "started", "error", "software_fault", True, mid="HB-M-000002"),
])


def test_timeline_orders_and_numbers_events():
    t = journeys.stage_timeline(EVENTS, T + pd.Timedelta(days=3))
    a = t[t["application_id"] == "PTR-0000001"]
    assert a["seq"].tolist() == list(range(1, 8))
    assert a["event_id"].tolist() == [f"PTE-00000000{i}" for i in range(1, 8)]


def test_t01_duplicate_event_counts_once():
    dup = EVENTS[EVENTS["event_id"] == "PTE-000000003"].copy()
    dup["ingested_at"] = dup["ingested_at"] + pd.Timedelta(minutes=5)
    doubled = pd.concat([EVENTS, dup], ignore_index=True)
    t = journeys.stage_timeline(doubled, T + pd.Timedelta(days=3))
    assert (t["event_id"] == "PTE-000000003").sum() == 1
    r = journeys.retries_after_error(doubled, T + pd.Timedelta(days=3)).set_index("application_id")
    assert r.loc["PTR-0000001", "software_errors"] == 2


def test_cutoff_uses_event_and_ingestion_time():
    late = frame([ev("PTE-000000099", "PTR-0000001", 15, "product_selected", "error", "software_fault", True, ingest_delay=60 * 48)])
    cutoff = T + pd.Timedelta(hours=2)
    t = journeys.stage_timeline(pd.concat([EVENTS, late], ignore_index=True), cutoff)
    assert "PTE-000000099" not in set(t["event_id"])
    assert "PTE-000000007" not in set(t["event_id"])  # occurs after the cutoff


def test_stage_dwell():
    cutoff = T + pd.Timedelta(days=3)
    d = journeys.stage_dwell(EVENTS, cutoff)
    a = d[d["application_id"] == "PTR-0000001"].set_index("stage")
    assert a.loc["started", "dwell_days"] == 10 / 1440
    assert round(a.loc["product_selected", "dwell_days"], 6) == round((60 * 24 - 10) / 1440, 6)
    assert a.loc["product_selected", "errors"] == 2 and a.loc["product_selected", "events"] == 5
    assert a.loc["documents_outstanding", "is_open"]
    assert a.loc["documents_outstanding", "dwell_days"] == 2.0


def test_retries_after_error():
    r = journeys.retries_after_error(EVENTS, T + pd.Timedelta(days=3)).set_index("application_id")
    one, two = r.loc["PTR-0000001"], r.loc["PTR-0000002"]
    assert (one["software_errors"], one["verified_faults"], one["retries_after_error"], one["recovered"]) == (2, 1, 2, True)
    assert (two["software_errors"], two["retries_after_error"], two["recovered"]) == (1, 0, False)


def test_terminal_stage_does_not_run_to_cutoff():
    done = frame([ev("PTE-000000021", "PTR-0000003", 0, "offer_accepted", "in_progress"),
                  ev("PTE-000000022", "PTR-0000003", 60, "completed", "completed")])
    d = journeys.stage_dwell(done, T + pd.Timedelta(days=10)).set_index("stage")
    assert d.loc["completed", "dwell_days"] == 0 and not d.loc["completed", "is_open"]


def test_kql_and_twin_define_the_same_columns():
    from pathlib import Path
    kql = (Path(__file__).resolve().parents[2] / "kql" / "03_journeys.kql").read_text(encoding="utf-8")
    for col in journeys.TIMELINE_COLS + ["dwell_days", "is_open", "retries_after_error", "recovered", "verified_faults"]:
        assert col in kql, col
