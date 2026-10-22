import pandas as pd
import pytest

from hbdata import fixtures, personas, snapshots, validation


def _snap_at(case, mortgage_ids=None, cutoff=None, extra=None):
    f = fixtures.frames_from(case["inputs"])
    cutoff = pd.Timestamp(cutoff or case["cutoff_at"])
    accts = f["mortgage_account"]
    req = pd.DataFrame({"mortgage_id": accts["mortgage_id"], "cycle_id": accts["cycle_id"], "cutoff_at": cutoff, "snapshot_kind": "live"})
    snap = snapshots.build_snapshots(f, req, cutoff, extra)
    return snap.set_index("mortgage_id")


def _norm(v):
    if v is None or (not isinstance(v, (list, str, bool)) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%dT%H:%M:%SZ")
    return v.item() if hasattr(v, "item") else v


def test_one_checkpoint_snapshot_per_cycle(checkpoint_snapshots, clean, cfg):
    accts = clean["mortgage_account"]
    assert len(checkpoint_snapshots) == (accts["deal_end_at"] - pd.Timedelta(days=90) <= cfg.as_of).sum()
    assert checkpoint_snapshots["snapshot_id"].is_unique
    assert (checkpoint_snapshots["days_to_deal_end"] == 90).all()
    assert (checkpoint_snapshots["snapshot_kind"] == "checkpoint").all()


def test_snapshot_has_no_outcome_columns(checkpoint_snapshots):
    leaks = [c for c in checkpoint_snapshots.columns if "outcome" in c or c == "label"]
    assert leaks == []


def test_inv2_truncated_history_gives_identical_snapshots(clean):
    """Removing every row not known at the cutoff must not change any snapshot value (INV-2)."""
    cutoff = pd.Timestamp("2026-05-01T00:00:00Z")
    req = snapshots.live_requests(clean["mortgage_account"], cutoff)
    req = req.sample(min(400, len(req)), random_state=7)
    full = snapshots.build_snapshots(clean, req, cutoff)
    rules = {"mortgage_account": ("recorded_at", "ingested_at"), "account_party": ("valid_from", "ingested_at"),
             "payment": ("posted_at", "ingested_at"), "transfer_request_event": ("occurred_at", "ingested_at"),
             "servicing_case_event": ("changed_at", "recorded_at"), "servicing_contact": ("occurred_at", "ingested_at")}
    cut = {k: v for k, v in clean.items()}
    for name, (ev, ing) in rules.items():
        df = clean[name]
        cut[name] = df[(df[ev] <= cutoff) & (df[ing] <= cutoff)].reset_index(drop=True)
    truncated = snapshots.build_snapshots(cut, req, cutoff)
    assert len(full) == len(req) > 100
    pd.testing.assert_frame_equal(full.drop(columns="created_at"), truncated.drop(columns="created_at"))


def test_cut01_fixture():
    case = fixtures.load_case("CUT01")
    s = _snap_at(case).iloc[0]
    for k in ("pt_request_count", "pt_latest_stage", "pt_software_errors_90d", "svc_unresolved_complaint",
              "svc_open_cases_at_cutoff", "svc_contacts_90d"):
        assert _norm(s[k]) == case["expected"][k], k
    for eid in case["expected"]["excluded_event_ids"]:
        assert eid not in s["evidence_ids"]


@pytest.mark.parametrize("scenario", ["missing", "stale", "fresh"])
def test_t03_stale_or_missing_payment_feed(scenario, cfg):
    case = fixtures.load_case("T03")["scenarios"][scenario]
    snap = _snap_at(case)
    for mid, expected in case["expected"].items():
        if mid == "feed_watermark":
            wm = validation.validate(fixtures.frames_from(case["inputs"]), pd.Timestamp(case["cutoff_at"]))["watermarks"]
            row = wm.set_index("dataset").loc[expected["dataset"]]
            assert bool(row["is_stale"]) is expected["is_stale"] and int(row["row_count"]) == expected["row_count"]
            continue
        for k, v in expected.items():
            assert _norm(snap.loc[mid, k]) == v, (scenario, k, snap.loc[mid, k])


def test_t04_reversal_in_snapshot(cfg):
    case = fixtures.load_case("T04")
    result = validation.validate(fixtures.frames_from(case["inputs"]), cfg.as_of)
    clean = result["clean"]
    req = pd.DataFrame({"mortgage_id": ["HB-M-800401"], "cycle_id": ["C02"], "cutoff_at": [pd.Timestamp(case["cutoff_at"])], "snapshot_kind": ["checkpoint"]})
    s = snapshots.build_snapshots(clean, req, cfg.as_of).iloc[0]
    for k, v in case["expected"]["snapshot"].items():
        assert _norm(s[k]) == v, k


def test_t07_fixture_distinguishes_repeat_contacts():
    case = fixtures.load_case("T07")
    snap = _snap_at(case)
    for mid, expected in case["expected"].items():
        for k, v in expected.items():
            assert _norm(snap.loc[mid, k]) == v, (mid, k)


def test_t07_generated_personas(checkpoint_snapshots):
    s = checkpoint_snapshots.set_index("mortgage_id")
    casey, robin = s.loc[personas.CASEY], s.loc[personas.ROBIN]
    assert (casey["svc_contacts_90d"], casey["svc_max_contacts_per_case_365d"], casey["svc_repeat_contacts_unresolved"]) == (3, 3, 2)
    assert (robin["svc_contacts_90d"], robin["svc_max_contacts_per_case_365d"], robin["svc_repeat_contacts_unresolved"]) == (3, 1, 0)
    assert casey["svc_unresolved_complaint"] and not robin["svc_unresolved_complaint"]


def test_alex_and_sam_personas(checkpoint_snapshots, clean):
    s = checkpoint_snapshots.set_index("mortgage_id")
    alex, sam = s.loc[personas.ALEX], s.loc[personas.SAM]
    assert alex["pt_software_errors_90d"] == 3 and alex["pt_unrecovered_software_error"]
    assert alex["svc_unresolved_complaint"]
    assert sam["ctx_reversals_180d"] == 1 and sam["ctx_arrears_amount"] == 0
    o = clean["observed_outcome"].set_index("mortgage_id")
    assert o.loc[personas.SAM, "outcome_type"] == "product_transfer" and o.loc[personas.SAM, "label"] == 0
    assert o.loc[personas.ALEX, "label_status"] == "pending"


def test_snapshots_are_immutable(tmp_path, checkpoint_snapshots):
    path = tmp_path / "fs.parquet"
    first = checkpoint_snapshots.head(20)
    assert snapshots.append_snapshots(first, path)["added"] == 20
    assert snapshots.append_snapshots(first, path)["added"] == 0
    changed = first.copy()
    changed.loc[changed.index[0], "balance"] = changed.loc[changed.index[0], "balance"] + 1
    with pytest.raises(ValueError, match="immutable"):
        snapshots.append_snapshots(changed, path)


def test_snapshot_id_is_deterministic():
    a = snapshots.snapshot_id("HB-M-000001", "C01", "2026-01-01T00:00:00Z")
    assert a == snapshots.snapshot_id("HB-M-000001", "C01", pd.Timestamp("2026-01-01", tz="UTC"))
    assert a != snapshots.snapshot_id("HB-M-000001", "C01", "2026-01-02T00:00:00Z")


def test_stale_payment_feed_in_generated_data(checkpoint_snapshots):
    stale = checkpoint_snapshots[checkpoint_snapshots["ctx_payment_feed_stale"]]
    assert len(stale) > 0
    assert stale["ctx_arrears_amount"].isna().all() and stale["ctx_missed_payments_180d"].isna().all()
