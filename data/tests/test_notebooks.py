"""Execute the Data and replay notebooks against a temporary output folder."""
import json
from pathlib import Path

import nbformat
import pandas as pd
import pytest
from nbclient import NotebookClient

from hbdata import generator, pipelines_local

NB_DIR = Path(__file__).resolve().parents[2] / "notebooks"


def _run(name: str, params: str, cwd: Path) -> nbformat.NotebookNode:
    nb = nbformat.read(NB_DIR / name, as_version=4)
    cell = next(c for c in nb.cells if "parameters" in c.metadata.get("tags", []))
    cell.source = params
    NotebookClient(nb, timeout=300, kernel_name="python3", resources={"metadata": {"path": str(cwd)}}).execute()
    return nb


@pytest.mark.slow
def test_notebooks_run_end_to_end(tmp_path):
    cfg = generator.GenConfig(per_cohort=60, open_cohort_size=40)
    frames, manifest = generator.generate(cfg)
    generator.write_exports(frames, manifest, tmp_path / "exports")
    pipelines_local.load_raw(tmp_path / "exports", tmp_path / "lakehouse", cfg.as_of)
    out = json.dumps(str(tmp_path))
    _run("01_validate_histories.ipynb", f"OUTPUT_ROOT = {out}\nRUN_AT = None", NB_DIR)
    assert (tmp_path / "lakehouse" / "clean" / "payment.parquet").exists()
    _run("02_feature_snapshots.ipynb", f"OUTPUT_ROOT = {out}\nMODE = 'checkpoint'\nCUTOFF = None\nMORTGAGE_IDS = None", NB_DIR)
    snap = pd.read_parquet(tmp_path / "lakehouse" / "features" / "feature_snapshot.parquet")
    assert len(snap) == len(frames["mortgage_account"])
