import pandas as pd

from hbdata import generator


def test_generation_is_deterministic(cfg, frames):
    again, _ = generator.generate(cfg)
    for name in generator.DATASETS:
        pd.testing.assert_frame_equal(frames[name], again[name])


def test_seed_changes_data(cfg, frames):
    other, _ = generator.generate(generator.GenConfig(seed=cfg.seed + 1, per_cohort=50, open_cohort_size=20))
    assert not frames["payment"]["amount_due"].head(20).equals(other["payment"]["amount_due"].head(20))


def test_nothing_after_as_of(cfg, frames):
    rules = {"payment": ("posted_at", "ingested_at"), "transfer_request_event": ("occurred_at", "ingested_at"),
             "servicing_case_event": ("changed_at", "recorded_at"), "servicing_contact": ("occurred_at", "ingested_at")}
    for name, cols in rules.items():
        for c in cols:
            assert (frames[name][c] <= cfg.as_of).all(), (name, c)


def test_inv4_labels(cfg, frames):
    o = frames["observed_outcome"]
    labelled = o[o["label_status"] == "labelled"]
    assert labelled["label"].notna().all()
    assert (labelled["observation_window_end_at"] <= cfg.as_of).all()
    assert (labelled["outcome_observed_at"] <= cfg.as_of).all()
    assert labelled["outcome_type"].isin(["external_refinance", "product_transfer", "continued_servicing"]).all()
    assert ((labelled["label"] == 1) == (labelled["outcome_type"] == "external_refinance")).all()
    other = o[o["label_status"] != "labelled"]
    assert other["label"].isna().all()
    pending = o[o["observation_window_end_at"] > cfg.as_of]
    assert (pending["label_status"] == "pending").all() and len(pending) > 200
    excluded = o[o["outcome_type"].isin(["sale_redemption", "bereavement_redemption", "redemption_unknown_reason"])]
    assert (excluded["label_status"] == "excluded").all() and len(excluded) > 50


def test_cohorts_and_label_rate(manifest, frames):
    rates = manifest["label_rate_by_cohort"]
    assert sorted(rates) == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    assert all(0.12 <= r <= 0.35 for r in rates.values()), rates
    assert frames["mortgage_account"]["mortgage_id"].is_unique


def test_external_refinance_happens_in_window(frames):
    o = frames["observed_outcome"]
    x = o[o["outcome_type"] == "external_refinance"]
    assert (x["outcome_at"] > x["checkpoint_at"]).all()
    assert (x["outcome_at"] <= x["observation_window_end_at"]).all()


def test_required_synthetic_cases_exist(frames, manifest):
    pay = frames["payment"]
    assert (pay["entry_type"] == "reversal").sum() > 20
    tr = frames["transfer_request_event"]
    assert (tr["status"] == "completed").sum() > 1000
    assert (tr["error_class"] == "software_fault").sum() > 100
    assert ((tr["ingested_at"] - tr["occurred_at"]) > pd.Timedelta(days=1)).sum() > 50  # delayed feed rows
    assert len(manifest["payment_feed_outages"]) == 5
    assert set(manifest["personas"]) == {"alex", "sam", "casey", "robin"}


def test_no_single_feature_leaks_label(checkpoint_snapshots, clean):
    o = clean["observed_outcome"]
    j = checkpoint_snapshots.merge(o[o["label_status"] == "labelled"][["mortgage_id", "label"]])
    y = j["label"].astype(int)
    for col in j.columns:
        if col in ("label",) or not pd.api.types.is_numeric_dtype(j[col]) or pd.api.types.is_bool_dtype(j[col]):
            continue
        r = j[col].astype(float).fillna(-1).rank()
        n1, n0 = y.sum(), len(y) - y.sum()
        auc = (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
        assert 0.25 < auc < 0.8, (col, auc)
