"""T05 against the Data and replay fixture data/fixtures/cases/T05.json."""
import json

import pandas as pd

from models_eval import config, gold, publish, scoring


def test_t05_fixture_failed_run_publishes_nothing_and_last_score_is_stale(tmp_path):
    case = json.loads((config.DATA_DIR / "fixtures" / "cases" / "T05.json").read_text(encoding="utf-8"))
    paths = config.paths(tmp_path)
    gold.upsert(paths.gold, "score_runs", pd.DataFrame(case["gold"]["score_runs"]))
    gold.append(paths.gold, "account_scores", pd.DataFrame(case["gold"]["account_scores"]))
    # Outbox rows for both runs. The failed run's row is a defensive case: scoring
    # never writes one, and the publisher must still refuse it.
    outbox = []
    for s in case["gold"]["account_scores"]:
        outbox.append({"event_id": scoring.event_id(s["run_id"], s["mortgage_id"], s["cycle_id"]), "run_id": s["run_id"],
                       "mortgage_id": s["mortgage_id"], "cycle_id": s["cycle_id"], "payload": json.dumps({"run_id": s["run_id"]}),
                       "status": "pending", "attempts": 0, "created_at": s["scored_at"]})
    gold.append(paths.gold, "risk_outbox", pd.DataFrame(outbox))

    result = publish.publish_pending(paths, now="2026-10-20T08:20:00Z")
    assert result["published"] == 1
    assert result["not_from_completed_run"] == 1
    sent = [json.loads(line) for line in (paths.eventstream / "risk_updated.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["run_id"] for e in sent] == ["RUN-20261020-0800"]
    assert case["expected"]["models_publishes_risk_updated_for_failed_run"] is False

    ctx = scoring.latest_scores(paths, "2026-10-20T08:20:00Z")
    expected = case["expected"]["score_context"]
    row = ctx.iloc[0]
    assert (row["run_id"], row["score"], row["score_state"]) == (expected["run_id"], expected["score"], expected["score_state"])
