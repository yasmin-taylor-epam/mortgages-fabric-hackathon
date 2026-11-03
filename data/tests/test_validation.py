import pandas as pd

from hbdata import fixtures, validation


def test_injected_anomalies_are_quarantined(validated, manifest):
    got = validated["quarantine_by_reason"]
    inj = manifest["injected_anomalies"]
    assert got["unknown_account"] == inj["unknown_account"]
    assert got["unknown_case_link"] == inj["unknown_case_link"]
    assert got["conflicting_case_link"] == inj["conflicting_case_link"]
    assert got["invalid_reversal"] == inj["invalid_reversal"]
    assert got["ingested_before_occurred"] == inj["ingested_before_occurred"]
    assert got["conflicting_duplicate"] >= 2 * inj["conflicting_duplicate"]
    assert len(validated["conflict_mortgages"]) == inj["conflicting_borrower_mapping"]


def test_quarantined_records_never_reach_clean(validated):
    q, clean = validated["quarantine"], validated["clean"]
    for name in ("payment", "transfer_request_event", "servicing_contact"):
        keys = set(q[(q["dataset"] == name) & (q["reason"] != "conflicting_duplicate")]["record_key"])
        pk = {"payment": "entry_id", "transfer_request_event": "event_id", "servicing_contact": "contact_id"}[name]
        assert not keys & set(clean[name][pk]), name
    conflict_keys = set(q[q["reason"] == "conflicting_duplicate"]["record_key"])
    assert not conflict_keys & set(clean["transfer_request_event"]["event_id"])


def test_clean_keys_are_unique(clean):
    assert clean["payment"]["entry_id"].is_unique
    assert clean["transfer_request_event"]["event_id"].is_unique
    assert clean["servicing_contact"]["contact_id"].is_unique


def test_personas_are_not_quarantined(validated):
    q = validated["quarantine"]
    assert not q["mortgage_id"].fillna("").str.startswith("HB-M-9000").any()


def test_t02_fixture_quarantines(cfg):
    case = fixtures.load_case("T02")
    result = validation.validate(fixtures.frames_from(case["inputs"]), cfg.as_of)
    assert result["quarantine_by_reason"] == case["expected"]["quarantine_by_reason"]
    ids = dict(zip(result["dimensions"]["mortgage"]["mortgage_id"], result["dimensions"]["mortgage"]["identity_status"]))
    assert ids == case["expected"]["identity_status"]


def test_t04_reversal_keeps_audit_trail(cfg):
    case = fixtures.load_case("T04")
    result = validation.validate(fixtures.frames_from(case["inputs"]), cfg.as_of)
    assert sorted(result["clean"]["payment"]["entry_id"]) == case["expected"]["clean_payment_entries"]
    q = result["quarantine"]
    assert dict(zip(q["record_key"], q["reason"])) == case["expected"]["quarantine"]


def test_exact_replay_of_a_load_is_deduplicated(frames, cfg):
    doubled = {k: pd.concat([v, v], ignore_index=True) for k, v in frames.items()}
    once = validation.validate(frames, cfg.as_of)
    twice = validation.validate(doubled, cfg.as_of)
    for name in ("payment", "transfer_request_event", "servicing_case_event", "servicing_contact"):
        assert len(once["clean"][name]) == len(twice["clean"][name]), name


def test_watermarks(validated):
    wm = validated["watermarks"].set_index("dataset")
    assert not wm.loc["payment", "is_stale"]
    assert wm.loc["payment", "row_count"] > 0


def test_case_status_at_cutoff_ignores_late_resolution():
    case = fixtures.load_case("CUT01")
    f = fixtures.frames_from(case["inputs"])
    st = validation.case_status_at(f["servicing_case_event"], pd.Timestamp(case["cutoff_at"]))
    assert st["latest_status"].tolist() == ["opened"]
    st_late = validation.case_status_at(f["servicing_case_event"], pd.Timestamp("2026-10-04T00:00:00Z"))
    assert st_late["latest_status"].tolist() == ["resolved"]
