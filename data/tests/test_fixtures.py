import json

from hbdata import fixtures, replay


def test_committed_case_fixtures_match_builder():
    for build in fixtures.CASE_BUILDERS:
        built = json.loads(json.dumps({"synthetic": True, "fictional_company": "HomeBridge", **build()}, default=str))
        committed = fixtures.load_case(built["id"])
        assert committed == built, f"{built['id']}.json drifted. Run: python -m hbdata fixtures"


def test_committed_demo_replay_matches_builder():
    for name, rows in fixtures.demo_events().items():
        committed = replay.read_jsonl(fixtures.FIXTURES_DIR / "replay" / name)
        assert committed == json.loads(json.dumps(rows)), name


def test_every_failure_case_has_a_fixture():
    ids = {p.stem for p in (fixtures.FIXTURES_DIR / "cases").glob("*.json")}
    assert {"T01", "T02", "T03", "T04", "T05", "T06", "T07", "CUT01"} <= ids
    for cid in ids:
        case = fixtures.load_case(cid)
        assert case["synthetic"] is True and case["rule"] and case["behaviour_owner"]


def test_fixture_ids_do_not_clash_with_generated_data(frames):
    ids = set(frames["mortgage_account"]["mortgage_id"])
    for p in (fixtures.FIXTURES_DIR / "cases").glob("*.json"):
        text = p.read_text(encoding="utf-8")
        for mid in ids:
            if mid.startswith("HB-M-8"):
                assert mid not in text
    assert not any(m.startswith("HB-M-8") for m in ids)
