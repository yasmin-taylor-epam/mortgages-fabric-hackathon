import json

import pandas as pd

from hbdata import contracts, fixtures, journeys, pipelines_local, replay, validation


def _context(case, at):
    result = validation.validate(fixtures.frames_from(case["inputs"]), pd.Timestamp(at))
    return pipelines_local.account_context(result["clean"], result["conflict_mortgages"], result["watermarks"], at)


def test_fixture_live_events_meet_contract():
    for cid in ("T01", "T02", "T04", "T05"):
        events = fixtures.load_case(cid)["live_events"]
        assert contracts.validate_records(events, "live_event") == [], cid
    for name in ("demo_alex.jsonl", "demo_alex_duplicate.jsonl"):
        assert contracts.validate_records(replay.read_jsonl(fixtures.FIXTURES_DIR / "replay" / name), "live_event") == []


def test_t01_replayed_event_gives_one_service_event(tmp_path):
    case = fixtures.load_case("T01")
    ctx = _context(case, "2026-10-20T09:00:00Z")
    stats = replay.ingest_local(case["live_events"], tmp_path, ctx, pd.Timestamp("2026-10-20T10:00:00Z"))
    raw, _ = replay.read_local(tmp_path)
    assert stats["accepted"] == len(raw) == case["expected"]["raw_event_rows"]
    se = replay.service_events(raw, ctx)
    assert len(se) == case["expected"]["service_event_count"]
    assert se[["event_id", "event_type", "cycle_id"]].to_dict("records") == case["expected"]["service_events"]
    t = journeys.stage_timeline(replay.transfer_events(raw), pd.Timestamp("2026-10-21T00:00:00Z"))
    assert len(t) == case["expected"]["journey_timeline_rows"]
    assert contracts.validate_frame(se, "service_event") == []


def test_t02_live_events_go_to_quarantine(tmp_path):
    case = fixtures.load_case("T02")
    ctx = _context(case, "2026-10-20T09:00:00Z")
    stats = replay.ingest_local(case["live_events"], tmp_path, ctx, pd.Timestamp("2026-10-20T10:00:00Z"))
    assert stats["accepted"] == case["expected"]["live_accepted"]
    assert stats["quarantine_reasons"] == case["expected"]["live_quarantine_by_reason"]
    _, q = replay.read_local(tmp_path)
    assert len(q) == 2  # rejected events stay visible


def test_t04_live_reversal_is_a_service_event(tmp_path):
    case = fixtures.load_case("T04")
    ctx = _context(case, "2026-10-20T09:00:00Z")
    replay.ingest_local(case["live_events"], tmp_path, ctx, pd.Timestamp("2026-10-20T11:00:00Z"))
    raw, _ = replay.read_local(tmp_path)
    se = replay.service_events(raw, ctx)
    got = [{"event_id": r["event_id"], "event_type": r["event_type"], "evidence_ids": list(r["evidence_ids"])} for r in se.to_dict("records")]
    assert got == case["expected"]["service_events"]


def test_t05_failed_run_marks_score_stale_and_service_events_continue(tmp_path):
    case = fixtures.load_case("T05")
    gold = tmp_path / "gold"
    gold.mkdir()
    for name, rows in case["gold"].items():
        df = pd.DataFrame(rows)
        for c in [c for c in df.columns if c.endswith("_at")]:
            df[c] = pd.to_datetime(df[c], utc=True)
        df.to_parquet(gold / f"{name}.parquet", index=False)
    sc = pipelines_local.score_context(gold, case["copied_at"])
    assert len(sc) == 1
    row = sc.iloc[0]
    for k, v in case["expected"]["score_context"].items():
        assert row[k] == v, k
    ctx = _context(case, case["copied_at"])
    replay.ingest_local(case["live_events"], tmp_path / "eh", ctx, pd.Timestamp(case["copied_at"]))
    raw, _ = replay.read_local(tmp_path / "eh")
    se = replay.service_events(raw, ctx)
    assert se[["event_id", "event_type"]].to_dict("records") == case["expected"]["service_events"]


def test_demo_replay_continues_alex(tmp_path, clean, validated, cfg):
    ctx = pipelines_local.account_context(clean, validated["conflict_mortgages"], validated["watermarks"], cfg.as_of)
    replay.ingest_local(replay.transfer_envelopes(clean["transfer_request_event"]), tmp_path, ctx, cfg.as_of)
    for name in ("demo_alex.jsonl", "demo_alex_duplicate.jsonl"):
        stats = replay.ingest_local(replay.read_jsonl(fixtures.FIXTURES_DIR / "replay" / name), tmp_path, ctx, cfg.as_of)
        assert stats["quarantined"] == 0
    raw, _ = replay.read_local(tmp_path)
    se = replay.service_events(raw, ctx)
    alex = se[se["mortgage_id"] == "HB-M-900001"]
    assert (alex["event_id"] == "PTE-900000002").sum() == 1
    assert "payment_support_requested" in set(alex["event_type"])
    r = journeys.retries_after_error(replay.transfer_events(raw), pd.Timestamp(cfg.as_of) + pd.Timedelta(days=2))
    a = r[r["mortgage_id"] == "HB-M-900001"].iloc[0]
    assert a["software_errors"] == 4 and not a["recovered"]


def test_account_context_has_no_balance(clean, validated, cfg):
    ctx = pipelines_local.account_context(clean, validated["conflict_mortgages"], validated["watermarks"], cfg.as_of)
    assert "balance" not in ctx.columns
    assert contracts.validate_frame(ctx.sample(500, random_state=3), "account_context") == []
    assert set(ctx[ctx["identity_status"] == "conflict"]["mortgage_id"]) == set(validated["conflict_mortgages"])
    assert (ctx["payment_support_status"] == "active").sum() > 0


def test_envelope_round_trip(clean):
    rows = clean["transfer_request_event"].head(30)
    envs = replay.transfer_envelopes(rows)
    assert contracts.validate_records(envs, "live_event") == []
    raw = pd.DataFrame([{**{k: e[k] for k in ("event_id", "event_type", "schema_version", "source_system", "mortgage_id", "application_id")},
                         "occurred_at": pd.Timestamp(e["occurred_at"]), "sent_at": pd.Timestamp(e["sent_at"]),
                         "payload": json.dumps(e["payload"]), "ingested_at": pd.Timestamp(e["simulated_ingested_at"])} for e in envs])
    back = replay.transfer_events(raw)
    assert back["event_id"].tolist() == rows["event_id"].tolist()
    assert back["stage"].tolist() == rows["stage"].tolist()
