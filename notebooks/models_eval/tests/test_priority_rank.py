"""Priority rank: every account at or above the alert threshold alerts. The rank only orders the review queue. Offline. No Fabric."""
import json
import shutil

import pandas as pd

from conftest import snapshot_frame  # noqa: F401  (keeps the conftest import path the same as the other tests)
from models_eval import config, gold, scoring

THRESHOLD = 0.5593


def scores_frame(values: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"mortgage_id": list(values), "cycle_id": "C01", "score": list(values.values())})


def twenty_two_above() -> dict[str, float]:
    qualify = {f"HB-M-{i:06d}": round(0.99 - i * 0.01, 6) for i in range(1, 23)}  # 22 accounts, all above the threshold
    below = {f"HB-M-{i:06d}": 0.10 for i in range(100, 108)}
    return qualify | below


def test_every_account_at_or_above_the_threshold_is_ranked_and_none_is_dropped():
    out = scoring.apply_priority_rank(scores_frame(twenty_two_above()), THRESHOLD)
    ranked = out[out["priority_rank"].notna()].sort_values("priority_rank")
    assert len(ranked) == 22  # 22 qualify, 22 alert. There is no cap.
    assert list(ranked["priority_rank"]) == list(range(1, 23))
    assert list(ranked["mortgage_id"]) == [f"HB-M-{i:06d}" for i in range(1, 23)]  # highest score first
    assert ranked["score"].is_monotonic_decreasing


def test_accounts_below_the_threshold_have_no_rank():
    out = scoring.apply_priority_rank(scores_frame(twenty_two_above()), THRESHOLD)
    below = out[out["score"] < THRESHOLD]
    assert len(below) == 8 and below["priority_rank"].isna().all()
    assert "over_capacity" not in out.columns  # nothing is flagged as suppressed


def test_few_or_none_qualify():
    seven = {f"HB-M-{i:06d}": 0.90 - i * 0.01 for i in range(1, 8)} | {"HB-M-000500": 0.2}
    assert int(scoring.apply_priority_rank(scores_frame(seven), THRESHOLD)["priority_rank"].notna().sum()) == 7
    assert scoring.apply_priority_rank(scores_frame({"HB-M-000001": 0.1}), THRESHOLD)["priority_rank"].isna().all()
    assert len(scoring.apply_priority_rank(scores_frame({}), THRESHOLD)) == 0


def test_a_score_equal_to_the_threshold_qualifies():
    out = scoring.apply_priority_rank(scores_frame({"HB-M-000001": THRESHOLD, "HB-M-000002": THRESHOLD - 1e-6}), THRESHOLD)
    assert list(out["priority_rank"].astype("object")) == [1, pd.NA]


def test_tie_break_is_stable_by_mortgage_id_then_cycle_and_ignores_row_order():
    tied = pd.DataFrame({"mortgage_id": [f"HB-M-{i:06d}" for i in range(12, 0, -1)] + ["HB-M-000005"], "cycle_id": ["C01"] * 12 + ["C02"],
                         "score": [0.8] * 13})  # 13 equal scores, one account has two cycles
    out = scoring.apply_priority_rank(tied, THRESHOLD)
    order = out.sort_values("priority_rank")[["mortgage_id", "cycle_id"]].apply(tuple, axis=1).tolist()
    assert order[:7] == [("HB-M-000001", "C01"), ("HB-M-000002", "C01"), ("HB-M-000003", "C01"), ("HB-M-000004", "C01"),
                         ("HB-M-000005", "C01"), ("HB-M-000005", "C02"), ("HB-M-000006", "C01")]
    expected = out.set_index(["mortgage_id", "cycle_id"])["priority_rank"].astype(int).to_dict()
    for seed in (1, 2, 3):
        shuffled = scoring.apply_priority_rank(tied.sample(frac=1, random_state=seed).reset_index(drop=True), THRESHOLD)
        assert shuffled.set_index(["mortgage_id", "cycle_id"])["priority_rank"].astype(int).to_dict() == expected


def test_there_is_no_alert_cap_setting():
    assert not hasattr(config, "alert_cap") and not hasattr(config, "ALERT_CAP_ENV")
    import inspect
    assert "cap" not in " ".join(inspect.signature(scoring.run_scoring).parameters)


def test_gold_table_keeps_the_new_column_and_old_rows_stay_readable():
    assert gold.TABLES["account_scores"]["columns"]["priority_rank"] == "Int64"
    old = pd.DataFrame({"account_key": ["a"], "mortgage_id": ["HB-M-000001"], "cycle_id": ["C01"], "run_id": ["R1"], "score": [0.5]})
    assert gold.coerce(old, "account_scores")["priority_rank"].isna().all()  # a run written before the rank has none


def _events(n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = pd.DataFrame({"mortgage_id": [f"HB-M-{i:06d}" for i in range(1, n + 1)], "cycle_id": "C01", "run_id": "RUN-X",
                         "snapshot_id": [f"SNP-{i:016x}" for i in range(1, n + 1)], "model_version": "hb-refi-clf:1",
                         "cluster_model_version": "hb-segments:1", "segment_id": "SEG-1",
                         "score": [round(0.99 - i * 0.01, 6) for i in range(n)],
                         "scored_at": pd.Timestamp("2026-11-05T17:21:00Z"), "cutoff_at": pd.Timestamp("2026-10-15T00:15:00Z")})
    snap = pd.DataFrame({"snapshot_id": base["snapshot_id"], "evidence_ids": [["APP-1"]] * n})
    return base, snap


def test_outbox_events_carry_the_rank_and_match_the_contract():
    config.ensure_hbdata()
    from hbdata import contracts as hb_contracts

    base, snap = _events(24)
    scores = scoring.apply_priority_rank(base, THRESHOLD)
    outbox = scoring.build_outbox(scores, snap, "RUN-X", pd.Timestamp("2026-11-05T17:21:00Z"))
    payloads = [json.loads(p) for p in outbox["payload"]]
    assert hb_contracts.validate_records(payloads, "risk_updated") == []
    assert len(payloads) == 24 and not any("over_capacity" in p for p in payloads)
    ranks = sorted(p["priority_rank"] for p in payloads if "priority_rank" in p)
    assert ranks == list(range(1, len(ranks) + 1)) and len(ranks) == int((base["score"] >= THRESHOLD).sum())


def test_an_older_event_without_the_rank_still_validates_and_the_old_flag_is_refused():
    config.ensure_hbdata()
    from hbdata import contracts as hb_contracts

    event = {"event_id": "RU-1", "event_type": "risk_updated", "schema_version": "1.0", "mortgage_id": "HB-M-000001", "cycle_id": "C01",
             "run_id": "R", "snapshot_id": "SNP-0000000000000001", "model_version": "m", "cluster_model_version": "c", "segment_id": "SEG-1",
             "scored_at": "2026-11-05T17:21:00.000000Z", "cutoff_at": "2026-10-15T00:15:00.000000Z", "target": "confirmed_external_refinancing",
             "score": 0.7, "evidence_ids": []}
    assert hb_contracts.validate_records([event], "risk_updated") == []
    assert hb_contracts.validate_records([{**event, "priority_rank": 3}], "risk_updated") == []
    assert hb_contracts.validate_records([{**event, "over_capacity": True}], "risk_updated") != []  # no suppression flag exists


def test_a_scoring_run_ranks_every_alert(released, tmp_path):
    """Real scoring on a private copy of the small synthetic lakehouse. A zero threshold makes every account qualify."""
    from models_eval import pipeline

    root = tmp_path / "hb"
    shutil.copytree(released["root"], root)  # the shared lakehouse stays untouched
    paths, rel, cutoff = config.paths(root), released["release"], released["as_of"]
    snap = pipeline.select_snapshots(paths, cutoff, "checkpoint", None)
    assert len(snap) > 10
    res = scoring.run_scoring(paths, snap, "manual", cutoff, rel["model_version"], rel["cluster_model_version"],
                              started_at=cutoff + pd.Timedelta(hours=5), alert_threshold=0.0)
    assert res["status"] == "completed" and res["alerts"] == len(snap) > 10  # more than ten alerts, none suppressed
    run = gold.read(paths.gold, "account_scores")
    run = run[run["run_id"] == res["run_id"]].sort_values("priority_rank")
    assert list(run["priority_rank"]) == list(range(1, len(snap) + 1)) and run["score"].is_monotonic_decreasing
    outbox = gold.read(paths.gold, "risk_outbox")
    sent = [json.loads(p) for p in outbox[outbox["run_id"] == res["run_id"]]["payload"]]
    assert len(sent) == len(snap) and all("priority_rank" in p for p in sent)
