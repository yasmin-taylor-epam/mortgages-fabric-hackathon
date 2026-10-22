"""End-to-end on a small lakehouse from the Data and replay generator.

Covers the release, scoring, append-only segments, INV-5 / T05, publication
retries with stable event IDs and the evaluation report.
"""
import json

import pandas as pd
import pytest

from models_eval import evaluate, gold, pipeline, publish, registry, scoring


class FailingSink:
    def send(self, events):
        raise ConnectionError("Eventstream unavailable (simulated)")


def _events(paths):
    p = paths.eventstream / "risk_updated.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_release_is_registered_and_pinned(released):
    rel, paths = released["release"], released["paths"]
    assert rel["registry"] == "mlflow"
    assert rel["model_version"].startswith("hb-refi-clf:") and rel["cluster_model_version"].startswith("hb-segments:")
    assert 2 <= rel["segments"]["k"] <= 5
    assert rel["champion"] in ("logistic_regression", "decision_tree")
    assert 0 < rel["alert_threshold"] < 1
    loaded = registry.load_release(paths, rel["model_version"])
    assert loaded["source"] == "mlflow"
    assert rel["split"]["dropped_after_release"] == {"train": 0, "validation": 0}


def test_scoring_runs_publish_append_and_fail_safely(released):
    paths, rel = released["paths"], released["release"]
    cutoff = released["as_of"]

    # Run 1: checkpoint run over open deal cycles. Publication fails first, then succeeds on retry.
    r1 = pipeline.run(paths, cutoff, "checkpoint", started_at=cutoff + pd.Timedelta(minutes=5), sink=FailingSink(),
                      model_version=rel["model_version"], cluster_model_version=rel["cluster_model_version"])
    assert r1["scoring"]["status"] == "completed"
    assert r1["publish"]["published"] == 0 and r1["publish"]["failed"] == r1["snapshots"] > 0
    outbox = gold.read(paths.gold, "risk_outbox")
    ids_before = set(outbox["event_id"])
    assert (outbox["status"] == "pending").all() and (outbox["attempts"] == 1).all()

    retry = publish.publish_pending(paths)
    assert retry["published"] == r1["snapshots"]
    outbox = gold.read(paths.gold, "risk_outbox")
    assert set(outbox["event_id"]) == ids_before
    assert (outbox["status"] == "published").all() and (outbox["attempts"] == 2).all()
    sent = _events(paths)
    assert {e["event_id"] for e in sent} == ids_before
    assert publish.publish_pending(paths)["published"] == 0  # nothing is sent twice by the publisher

    from hbdata import contracts as hb_contracts
    assert hb_contracts.validate_records(sent, "risk_updated") == []
    assert all(e["target"] == "confirmed_external_refinancing" and 0 <= e["score"] <= 1 for e in sent)

    segs_1 = gold.read(paths.gold, "segment_membership")

    # Run 2: scheduled live run. Segment rows are appended; run-1 rows do not change.
    r2 = pipeline.run(paths, cutoff + pd.Timedelta(minutes=15), "scheduled", started_at=cutoff + pd.Timedelta(minutes=16))
    assert r2["scoring"]["status"] == "completed"
    segs_2 = gold.read(paths.gold, "segment_membership")
    assert len(segs_2) == len(segs_1) + r2["snapshots"]
    pd.testing.assert_frame_equal(segs_2.iloc[:len(segs_1)].reset_index(drop=True), segs_1)
    assert set(segs_2["cluster_model_version"]) == {rel["cluster_model_version"]}
    assert segs_2["valid_to"].isna().all()

    attr = gold.read(paths.gold, "feature_attributions")
    assert attr.groupby(["account_key", "run_id"])["rank"].min().eq(1).all()
    assert set(attr["feature_name"]) <= set(rel["feature_names"])

    # Run 3: fails after writing outputs (T05). No outbox rows, no events, last score stale.
    published_before = len(_events(paths))
    r3 = pipeline.run(paths, cutoff + pd.Timedelta(minutes=30), "scheduled", started_at=cutoff + pd.Timedelta(minutes=31),
                      inject_failure="after_outputs")
    assert r3["scoring"]["status"] == "failed"
    runs = gold.read(paths.gold, "score_runs").set_index("run_id")
    assert runs.loc[r3["scoring"]["run_id"], "status"] == "failed"
    assert pd.isna(runs.loc[r3["scoring"]["run_id"], "completed_at"])
    assert r3["scoring"]["run_id"] not in set(gold.read(paths.gold, "risk_outbox")["run_id"])
    assert publish.publish_pending(paths)["published"] == 0
    assert len(_events(paths)) == published_before

    ctx = scoring.latest_scores(paths, cutoff + pd.Timedelta(minutes=35))
    assert len(ctx) and (ctx["score_state"] == "stale").all()
    assert set(ctx["run_id"]) <= {r1["scoring"]["run_id"], r2["scoring"]["run_id"]}


def test_failure_before_outputs_writes_nothing(released):
    paths = released["paths"]
    snap = pipeline.checkpoint_snapshots(paths, released["as_of"])
    before = {t: len(gold.read(paths.gold, t)) for t in ("account_scores", "segment_membership", "risk_outbox")}
    with pytest.raises(scoring.ScoringRunFailed):
        scoring.run_scoring(paths, snap, "manual", released["as_of"], started_at=released["as_of"] + pd.Timedelta(hours=2),
                            inject_failure="before_outputs")
    after = {t: len(gold.read(paths.gold, t)) for t in before}
    assert after == before


def test_scoring_rejects_inv2_breach(released):
    paths = released["paths"]
    snap = pipeline.checkpoint_snapshots(paths, released["as_of"]).head(3).copy()
    snap.loc[0, "source_watermark"] = json.dumps({"payment": "2099-01-01T00:00:00+00:00"})
    with pytest.raises(scoring.ScoringRunFailed, match="INV-2"):
        scoring.run_scoring(paths, snap, "manual", released["as_of"], started_at=released["as_of"] + pd.Timedelta(hours=3))


def test_evaluation_report(released):
    paths = released["paths"]
    rep = evaluate.evaluate_release(paths)
    assert set(rep["models"]) == {"rules_baseline", "logistic_regression", "decision_tree"}
    for m in rep["models"].values():
        for key in ("accuracy", "precision", "recall", "roc_auc", "calibration", "lift_at_10_by_cohort", "vs_baseline"):
            assert key in m
        assert {c["cohort_id"] for c in m["lift_at_10_by_cohort"]} == {"2026-05", "2026-06"}
    files = evaluate.write_report(paths, rep)
    text = open(files["markdown"], encoding="utf-8").read()
    assert "Synthetic data" in text and "Lift at ten reviews per run" in text
