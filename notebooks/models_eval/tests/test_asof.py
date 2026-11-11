"""One as-of date in Models and evaluation: HB_AS_OF gives a run a simulated start and publishing a clock. Offline."""
import pandas as pd
import pytest

from models_eval import asof


def test_run_start_is_one_minute_after_the_as_of_time(monkeypatch):
    monkeypatch.delenv("HB_AS_OF", raising=False)
    assert asof.as_of() is None and asof.run_start() is None
    monkeypatch.setenv("HB_AS_OF", "2026-10-16T09:00:00Z")
    assert asof.as_of() == pd.Timestamp("2026-10-16T09:00:00Z")
    assert asof.run_start() == asof.now() == pd.Timestamp("2026-10-16T09:01:00Z")
    monkeypatch.setenv("HB_AS_OF", "bad")
    with pytest.raises(ValueError, match="HB_AS_OF"):
        asof.run_start()


def test_a_scoring_run_without_a_start_uses_the_as_of_time(released, monkeypatch, tmp_path):
    import shutil

    from models_eval import config, pipeline, scoring

    root = tmp_path / "hb"
    shutil.copytree(released["root"], root)
    paths, rel, cutoff = config.paths(root), released["release"], released["as_of"]
    snap = pipeline.select_snapshots(paths, cutoff, "checkpoint", None)
    monkeypatch.setenv("HB_AS_OF", "2026-10-16T09:00:00Z")
    res = scoring.run_scoring(paths, snap, "manual", cutoff, rel["model_version"], rel["cluster_model_version"])
    assert res["run_id"] == "RUN-20261016-090100"
    runs = pd.read_parquet(paths.gold / "score_runs.parquet").set_index("run_id")
    assert pd.Timestamp(runs.loc[res["run_id"], "started_at"]) == pd.Timestamp("2026-10-16T09:01:00Z")


def test_a_run_records_the_accounts_it_covers():
    snap = pd.DataFrame({"mortgage_id": ["M2", "M1", "M1"], "cycle_id": ["C1", "C1", "C1"]})
    from models_eval import scoring

    assert scoring.scope_keys(snap) == ",M1|C1,M2|C1,"
    assert scoring.scope_keys(snap.iloc[:0]) is None
