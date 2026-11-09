"""The alert threshold comes from a named rule and is saved with the release. Offline. Synthetic values."""
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import f1_score

from models_eval import config, evaluate, models, policy, scoring, thresholds

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]


def labelled(n=400, base=0.2, seed=3):
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < base).astype(int)
    p = np.clip(0.15 + 0.35 * y + rng.normal(0, 0.18, n), 0.001, 0.999)
    return y, p


def test_max_f1_matches_a_brute_force_search_and_keeps_the_higher_tie():
    y, p = labelled()
    best = max(((round(float(f1_score(y, (p >= t).astype(int), zero_division=0)), 6), float(t))
                for t in np.unique(np.round(p, 6)) if 0 < (p >= t).sum() < len(p)))
    assert thresholds.choose(y, p, "max_f1") == best[1]
    out = thresholds.freeze(y, p, "max_f1")
    assert out["rule_name"] == "max_f1" and out["rule"] == "max F1 on validation" and out["rule_params"] == {}


def test_min_cost_moves_with_the_cost_ratio_and_needs_both_costs():
    y, p = labelled()
    cheap_miss = thresholds.choose(y, p, "min_cost", cost_missed_leaver=1.0, cost_review=1.0)
    dear_miss = thresholds.choose(y, p, "min_cost", cost_missed_leaver=20.0, cost_review=1.0)
    assert dear_miss < cheap_miss  # a missed leaver costing more means a lower bar, so more alerts
    for kw in ({}, {"cost_missed_leaver": 5.0}, {"cost_review": 1.0}):
        with pytest.raises(ValueError, match="needs cost_missed_leaver and cost_review"):
            thresholds.choose(y, p, "min_cost", **kw)
    with pytest.raises(ValueError):
        thresholds.choose(y, p, "min_cost", cost_missed_leaver=0, cost_review=1)
    free_reviews = thresholds.choose(y, p, "min_cost", cost_missed_leaver=5.0, cost_review=0.0)  # reviews cost nothing: alert more than any other case
    assert (p >= free_reviews).mean() > (p >= dear_miss).mean()
    assert thresholds.freeze(y, p, "min_cost", 5.0, 1.0)["rule_params"] == {"cost_missed_leaver": 5.0, "cost_review": 1.0}


def test_unknown_rule_is_refused_and_the_default_is_max_f1(monkeypatch):
    y, p = labelled()
    with pytest.raises(ValueError, match="Unknown threshold rule"):
        thresholds.choose(y, p, "by_hand")
    for var in ("HB_THRESHOLD_RULE", "HB_COST_MISSED_LEAVER", "HB_COST_REVIEW"):
        monkeypatch.delenv(var, raising=False)
    assert config.threshold_rule() == config.ThresholdRule("max_f1", None, None)
    monkeypatch.setenv("HB_THRESHOLD_RULE", "min_cost")
    monkeypatch.setenv("HB_COST_MISSED_LEAVER", "8")
    monkeypatch.setenv("HB_COST_REVIEW", "2")
    assert config.threshold_rule() == config.ThresholdRule("min_cost", 8.0, 2.0) and config.threshold_rule("max_f1").name == "max_f1"
    with pytest.raises(ValueError):
        config.threshold_rule("guess")


def test_models_freeze_threshold_still_works_and_never_alerts_everyone():
    y, p = labelled()
    t = models.freeze_threshold(y, p)
    assert 0 < t["validation_alert_rate"] < 1 and t["threshold"] == round(thresholds.choose(y, p), 4)


def test_options_table_compares_without_choosing():
    yv, pv = labelled(500, seed=1)
    yt, pt = labelled(800, seed=2)
    chosen = thresholds.choose(yv, pv)
    rows = thresholds.options(yv, pv, yt, pt, chosen, "max_f1", open_scores=pt[:300], retired=float(np.sort(pv)[::-1][9]))
    names = [r["option"] for r in rows]
    assert names[0] == "chosen rule (max_f1)" and rows[0]["threshold"] == round(chosen, 4)
    assert any(n.startswith("retired:") and "comparison only" in n for n in names) and len(rows) == 6
    r80 = next(r for r in rows if r["option"].startswith("80% recall"))
    assert r80["validation"]["recall"] >= 0.8
    top5 = next(r for r in rows if r["option"].startswith("top 5%"))
    assert 0.04 <= top5["validation"]["alert_share"] <= 0.07
    retired = rows[-1]
    assert retired["validation"]["alerts"] >= 10 and rows[0]["open_accounts"]["accounts"] == 300
    assert all(0 <= r["test"]["alert_share"] <= 1 for r in rows)


def test_the_release_stores_the_rule_and_no_capacity_number(released):
    rel = released["release"]
    assert rel["alert_threshold_rule"] == "max_f1" and rel["alert_threshold_params"] == {}
    assert rel["alert_threshold"] == rel["validation"][rel["champion"]]["threshold"]
    assert "review_capacity_threshold" not in rel and "review_capacity_note" not in rel


def test_the_policy_is_written_from_the_release_and_nobody_types_the_number(released):
    paths, rel = released["paths"], released["release"]
    out = policy.write(paths)
    doc = json.loads(Path(out["file"]).read_text(encoding="utf-8"))
    assert doc["risk_threshold"] == rel["alert_threshold"] and doc["threshold_rule"] == "max_f1"
    assert doc["version"] == f"routing-policy-{rel['release_id']}" and doc["max_score_age_minutes"] == config.MAX_SCORE_AGE_MINUTES
    assert "placeholder" in doc["_note"] and out["file"].replace("\\", "/").endswith("policy/routing-policy.json")


def test_old_release_files_without_the_rule_name_still_give_a_policy(released, monkeypatch):
    rel = {k: v for k, v in released["release"].items() if k not in ("alert_threshold_rule", "alert_threshold_params")}
    rel["review_capacity_threshold"] = 0.5593  # what an older release carried
    monkeypatch.setattr(policy.registry, "load_release", lambda paths, version=None: {"release": rel})
    doc = policy.build(released["paths"])
    assert doc["threshold_rule"] == "max_f1" and doc["risk_threshold"] == rel["alert_threshold"] != 0.5593


def test_scoring_uses_the_release_threshold_by_default(released, tmp_path):
    from models_eval import pipeline

    root = tmp_path / "hb"
    shutil.copytree(released["root"], root)
    paths, rel, cutoff = config.paths(root), released["release"], released["as_of"]
    snap = pipeline.select_snapshots(paths, cutoff, "checkpoint", None)
    res = scoring.run_scoring(paths, snap, "manual", cutoff, rel["model_version"], rel["cluster_model_version"],
                              started_at=cutoff + pd.Timedelta(hours=7))
    assert res["alert_threshold"] == rel["alert_threshold"]
    assert res["alerts"] == int((pd.read_parquet(paths.gold / "account_scores.parquet").query("run_id == @res['run_id']")["score"] >= rel["alert_threshold"]).sum())


def test_the_evaluation_report_has_the_options_and_no_capacity_threshold(released):
    rep = evaluate.evaluate_release(released["paths"])
    assert rep["alert_threshold_rule"] == "max_f1" and rep["lift_k"] == 10
    assert "review_capacity_threshold" not in rep and "reviews_per_run" not in rep
    assert len(rep["threshold_options"]) == 6 and rep["threshold_options"][0]["threshold"] == round(rep["alert_threshold"], 4)
    md = evaluate.to_markdown(rep)
    assert "Alert threshold options (for comparison)" in md and "retired" in md and "ten-review" not in md and "Lift at 10" in md


def test_no_code_treats_ten_reviews_as_a_capacity():
    banned = ("review_capacity", "REVIEWS_PER_RUN", "reviews_per_run")
    for folder in (PKG, REPO / "action" / "homebridge_action"):
        for f in folder.rglob("*.py"):
            if "tests" in f.parts:
                continue
            assert not any(b in f.read_text(encoding="utf-8") for b in banned), f
