"""Lift, thresholds, the rules baseline and the append-only Gold store, on hand-written values."""
import numpy as np
import pandas as pd
import pytest
from conftest import snapshot_frame

from models_eval import evaluate, features, gold, models, scoring


def test_lift_at_ten_hand_checked():
    # 100 accounts, 20 positives (base rate 0.2). The 10 highest scores hold 6 positives.
    y = np.array([1] * 6 + [0] * 4 + [1] * 14 + [0] * 76)
    p = np.linspace(1.0, 0.01, 100)
    r = evaluate.lift_at_k(y, p, 10)
    assert r == {"k": 10, "precision_at_k": 0.6, "base_rate": 0.2, "lift": 3.0}


def test_lift_at_ten_shares_tied_places_evenly():
    # 5 accounts above the cut (3 positives); 10 tied at the cut (2 positives) share 5 places.
    y = np.array([1, 1, 1, 0, 0] + [1, 1] + [0] * 8 + [0] * 85)
    p = np.array([0.9] * 5 + [0.5] * 10 + [0.1] * 85)
    assert evaluate.expected_positives_at_k(y, p, 10) == pytest.approx(3 + 5 * 0.2)


def test_threshold_never_alerts_everyone():
    y = np.array([0, 0, 0, 1, 1])
    p = np.array([0.0, 0.0, 0.25, 0.25, 0.5])
    t = models.freeze_threshold(y, p)
    assert t["threshold"] > 0.0
    assert t["validation_alert_rate"] < 1.0


def test_rules_baseline_scores_request_friction():
    snap = snapshot_frame([
        {},
        {"pt_failed_attempts_30d": 2, "pt_unrecovered_software_error": True, "pt_days_at_current_stage": 40.0,
         "pt_abandoned_or_rejected_count": 1},
        {"pt_days_at_current_stage": 20.0},
    ])
    X = features.build_matrix(snap)
    m = models.RulesBaseline(stall_days=21).fit(X)
    assert list(models.proba(m, X)) == [0.0, 1.0, 0.0]


def test_event_id_is_stable():
    assert scoring.event_id("RUN-1", "HB-M-000001", "C01") == scoring.event_id("RUN-1", "HB-M-000001", "C01")
    assert scoring.event_id("RUN-1", "HB-M-000001", "C01") != scoring.event_id("RUN-2", "HB-M-000001", "C01")


def _seg_rows(run_id, segment):
    ts = pd.Timestamp("2026-10-15T00:00:00Z")
    return pd.DataFrame([{"account_key": "HB-M-000001|C01", "mortgage_id": "HB-M-000001", "cycle_id": "C01", "run_id": run_id,
                          "segment_id": segment, "cluster_model_version": "hb-segments:1", "assigned_at": ts, "valid_from": ts,
                          "valid_to": None}])


def test_segment_membership_is_append_only(tmp_path):
    gold.append(tmp_path, "segment_membership", _seg_rows("RUN-1", "SEG-1"))
    gold.append(tmp_path, "segment_membership", _seg_rows("RUN-2", "SEG-2"))
    with pytest.raises(gold.AppendOnlyError):
        gold.append(tmp_path, "segment_membership", _seg_rows("RUN-1", "SEG-2"))
    with pytest.raises(gold.AppendOnlyError):
        gold.upsert(tmp_path, "segment_membership", _seg_rows("RUN-1", "SEG-2"))
    rows = gold.read(tmp_path, "segment_membership")
    assert list(rows["segment_id"]) == ["SEG-1", "SEG-2"]
    assert rows["valid_to"].isna().all()
