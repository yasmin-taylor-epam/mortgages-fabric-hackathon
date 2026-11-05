import json
import sys
from pathlib import Path

import pandas as pd
import pytest

NOTEBOOKS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(NOTEBOOKS_DIR))

from models_eval import config, features  # noqa: E402

config.ensure_hbdata()


def snapshot_row(**overrides) -> dict:
    """One hand-written, synthetic feature snapshot row. Not a data generator."""
    row = {
        "snapshot_id": "SNP-0000000000000001", "mortgage_id": "HB-M-000001", "cycle_id": "C01", "cohort_id": "2026-01",
        "cutoff_at": pd.Timestamp("2025-10-03T00:00:00Z"), "snapshot_kind": "checkpoint", "feature_version": features.FEATURE_VERSION,
        "source_watermark": json.dumps({"payment": "2025-10-02T00:00:00+00:00", "servicing_contact": None}),
        "evidence_ids": ["APP-1"], "days_to_deal_end": 90.0,
    }
    for c in features.NUMERIC:
        row[c] = 0.0
    for c in features.BOOLEAN:
        row[c] = False
    row["pt_latest_stage"] = None
    row["pt_latest_status"] = None
    row["rate_gap_pct"] = 2.5
    row["deal_length_months"] = 24
    row.update(overrides)
    return row


def snapshot_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([snapshot_row(**r) for r in rows])


@pytest.fixture(scope="session")
def lakehouse(tmp_path_factory):
    """A small synthetic lakehouse built with the Data and replay package (data/hbdata)."""
    from hbdata import generator, io, pipelines_local, snapshots, validation

    root = tmp_path_factory.mktemp("hb")
    cfg = generator.GenConfig(per_cohort=150, open_cohort_size=60)
    frames, manifest = generator.generate(cfg)
    generator.write_exports(frames, manifest, root / "exports")
    pipelines_local.load_raw(root / "exports", root / "lakehouse", cfg.as_of)
    result = validation.validate(pipelines_local.read_raw(root / "lakehouse"), cfg.as_of)
    validation.write_outputs(result, root / "lakehouse")
    clean = pipelines_local.read_clean(root / "lakehouse")
    req = snapshots.checkpoint_requests(clean["mortgage_account"], cfg.as_of)
    snap = snapshots.build_snapshots(clean, req, cfg.as_of)
    snapshots.append_snapshots(snap, root / "lakehouse" / "features" / "feature_snapshot.parquet")
    io.write_table(clean["observed_outcome"], root / "lakehouse" / "features" / "observed_outcome.parquet", "observed_outcome")
    return {"root": root, "as_of": pd.Timestamp(cfg.as_of)}


@pytest.fixture(scope="session")
def released(lakehouse):
    from models_eval import train

    paths = config.paths(lakehouse["root"])
    release = train.train_release(paths, created_at="2026-10-15T00:00:00Z", use_mlflow=True, n_boot=5)
    return {"paths": paths, "release": release, **lakehouse}
