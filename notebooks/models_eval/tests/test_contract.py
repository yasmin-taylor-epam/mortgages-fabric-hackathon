"""INV-2, INV-4, the release-date rule and borrower exclusion, on hand-written rows."""
import json

import pandas as pd
import pytest
from conftest import snapshot_frame

from models_eval import contract, features
from models_eval.config import SplitConfig


def test_inv2_watermark_after_cutoff_is_rejected():
    snap = snapshot_frame([
        {"snapshot_id": "SNP-0000000000000001"},
        {"snapshot_id": "SNP-0000000000000002", "mortgage_id": "HB-M-000002",
         "source_watermark": json.dumps({"payment": "2025-10-03T00:00:01+00:00"})},
    ])
    assert contract.inv2_violations(snap) == ["SNP-0000000000000002"]
    with pytest.raises(contract.InvariantError, match="INV-2"):
        contract.check_snapshots(snap)


def test_inv2_snapshot_after_run_cutoff_is_rejected():
    snap = snapshot_frame([{}])
    contract.check_snapshots(snap, run_cutoff="2025-10-03T00:00:00Z")
    with pytest.raises(contract.InvariantError, match="run cutoff"):
        contract.check_snapshots(snap, run_cutoff="2025-10-02T23:59:59Z")


def test_feature_version_mismatch_is_rejected():
    with pytest.raises(contract.InvariantError, match="feature_version"):
        contract.check_snapshots(snapshot_frame([{"feature_version": "fv0"}]), feature_version=features.FEATURE_VERSION)


def _outcomes(rows):
    base = {"outcome_observed_at": pd.Timestamp("2026-03-01T00:00:00Z")}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_inv4_unknown_and_excluded_outcomes_never_become_retained():
    out = _outcomes([
        {"mortgage_id": "HB-M-000001", "cycle_id": "C01", "outcome_type": "external_refinance", "label_status": "labelled", "label": 1},
        {"mortgage_id": "HB-M-000002", "cycle_id": "C01", "outcome_type": "product_transfer", "label_status": "labelled", "label": 0},
        {"mortgage_id": "HB-M-000003", "cycle_id": "C01", "outcome_type": "not_yet_observed", "label_status": "pending", "label": None},
        {"mortgage_id": "HB-M-000004", "cycle_id": "C01", "outcome_type": "redemption_unknown_reason", "label_status": "excluded", "label": None},
        {"mortgage_id": "HB-M-000005", "cycle_id": "C01", "outcome_type": "sale_redemption", "label_status": "excluded", "label": None},
    ])
    lab = contract.labels(out)
    assert set(lab["mortgage_id"]) == {"HB-M-000001", "HB-M-000002"}
    assert dict(zip(lab["mortgage_id"], lab["label"])) == {"HB-M-000001": 1, "HB-M-000002": 0}


def test_inv4_inconsistent_label_is_rejected():
    out = _outcomes([{"mortgage_id": "HB-M-000001", "cycle_id": "C01", "outcome_type": "continued_servicing",
                      "label_status": "labelled", "label": 1}])
    with pytest.raises(contract.InvariantError, match="INV-4"):
        contract.labels(out)
    out = _outcomes([{"mortgage_id": "HB-M-000001", "cycle_id": "C01", "outcome_type": "sale_redemption",
                      "label_status": "labelled", "label": 0}])
    with pytest.raises(contract.InvariantError, match="INV-4"):
        contract.labels(out)


def test_labels_observed_after_release_are_dropped():
    out = _outcomes([
        {"mortgage_id": "HB-M-000001", "cycle_id": "C01", "outcome_type": "external_refinance", "label_status": "labelled", "label": 1,
         "outcome_observed_at": pd.Timestamp("2026-05-31T23:59:59Z")},
        {"mortgage_id": "HB-M-000002", "cycle_id": "C01", "outcome_type": "product_transfer", "label_status": "labelled", "label": 0,
         "outcome_observed_at": pd.Timestamp("2026-06-01T00:00:00Z")},
    ])
    assert list(contract.labels(out, release_at="2026-06-01T00:00:00Z")["mortgage_id"]) == ["HB-M-000001"]


def _split_frame():
    rows = []
    n = 0
    for cohort in ("2026-01", "2026-04", "2026-05"):
        for label in (0, 1, 0, 1):
            n += 1
            rows.append({"mortgage_id": f"HB-M-{n:06d}", "cycle_id": "C01", "cohort_id": cohort, "label": label,
                         "outcome_observed_at": pd.Timestamp(f"{cohort}-20T00:00:00Z") + pd.Timedelta(days=40)})
    return pd.DataFrame(rows)


def test_split_removes_training_rows_that_share_a_borrower_with_test():
    frame = _split_frame()
    # HB-M-000001 (train) and HB-M-000009 (test) share a borrower.
    parties = pd.DataFrame([
        {"mortgage_id": "HB-M-000001", "party_id": "HB-P-1", "mapping_status": "approved"},
        {"mortgage_id": "HB-M-000009", "party_id": "HB-P-1", "mapping_status": "approved"},
        {"mortgage_id": "HB-M-000002", "party_id": "HB-P-2", "mapping_status": "approved"},
    ])
    frame = pd.concat([frame, frame.iloc[[0]].assign(mortgage_id="HB-M-000013", label=0)], ignore_index=True)
    cfg = SplitConfig(train_cohorts=("2026-01",), validation_cohorts=("2026-04",), test_cohorts=("2026-05",),
                      release_at="2026-06-01T00:00:00Z")
    parts, summary = contract.split(frame, parties, cfg)
    assert "HB-M-000001" not in set(parts["train"]["mortgage_id"])
    assert summary["dropped_shared_borrower"]["train"] == 1
    assert not set(parts["train"]["mortgage_id"]) & set(parts["test"]["mortgage_id"])
    assert set(parts["test"]["cohort_id"]) == {"2026-05"}


def test_split_drops_validation_labels_observed_after_release():
    frame = _split_frame()
    frame.loc[frame["cohort_id"] == "2026-04", "outcome_observed_at"] = pd.Timestamp("2026-07-01T00:00:00Z")
    frame = pd.concat([frame, frame[frame["cohort_id"] == "2026-04"].assign(
        mortgage_id=lambda d: d["mortgage_id"].str.replace("HB-M-0000", "HB-M-0099"),
        outcome_observed_at=pd.Timestamp("2026-05-01T00:00:00Z"))], ignore_index=True)
    cfg = SplitConfig(train_cohorts=("2026-01",), validation_cohorts=("2026-04",), test_cohorts=("2026-05",),
                      release_at="2026-06-01T00:00:00Z")
    parts, summary = contract.split(frame, None, cfg)
    assert summary["dropped_after_release"]["validation"] == 4
    assert (pd.to_datetime(parts["validation"]["outcome_observed_at"], utc=True) < cfg.release_ts).all()


def test_model_matrix_excludes_context_and_outcome_columns():
    snap = snapshot_frame([{"ctx_payment_support_open": True, "label": 1, "balance": 1e6}])
    X = features.build_matrix(snap)
    assert not [c for c in X.columns if c.startswith("ctx_") or c in ("label", "balance", "days_to_deal_end")]
    assert list(X.columns) == features.feature_names()
