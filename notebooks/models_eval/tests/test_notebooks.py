"""Execute notebooks 03, 04 and 05 against a copy of the small synthetic lakehouse."""
import json
import shutil

import nbformat
import pandas as pd
import pytest
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

from conftest import NOTEBOOKS_DIR


def _run(name: str, params: str) -> nbformat.NotebookNode:
    nb = nbformat.read(NOTEBOOKS_DIR / name, as_version=4)
    cell = next(c for c in nb.cells if "parameters" in c.metadata.get("tags", []))
    cell.source = params
    NotebookClient(nb, timeout=600, kernel_name="python3", resources={"metadata": {"path": str(NOTEBOOKS_DIR)}}).execute()
    return nb


@pytest.mark.slow
def test_notebooks_run_end_to_end(lakehouse, tmp_path):
    root = tmp_path / "out"
    shutil.copytree(lakehouse["root"], root, ignore=shutil.ignore_patterns("gold", "models", "mlflow", "eventstream", "reports"))
    out = json.dumps(str(root))
    as_of = lakehouse["as_of"]

    _run("03_train_models.ipynb", f"OUTPUT_ROOT = {out}\nUSE_MLFLOW = True\nN_BOOT = 5")
    assert list((root / "models" / "releases").glob("*/release.json"))

    _run("04_score_and_segment.ipynb", f"OUTPUT_ROOT = {out}\nRUN_KIND = 'checkpoint'\nCUTOFF = None\n"
         f"STARTED_AT = '{(as_of + pd.Timedelta(minutes=5)).isoformat()}'\nMODEL_VERSION = None\nCLUSTER_MODEL_VERSION = None\n"
         "MORTGAGE_IDS = None\nBUILD_LIVE_SNAPSHOTS = False\nINJECT_FAILURE = None\nPUBLISH = True\nPUBLISH_ONLY = False")
    runs = pd.read_parquet(root / "lakehouse" / "gold" / "score_runs.parquet")
    assert list(runs["status"]) == ["completed"]
    events = (root / "eventstream" / "risk_updated.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) > 0

    with pytest.raises(CellExecutionError, match="ScoringRunFailed"):
        _run("04_score_and_segment.ipynb", f"OUTPUT_ROOT = {out}\nRUN_KIND = 'scheduled'\n"
             f"CUTOFF = '{(as_of + pd.Timedelta(minutes=15)).isoformat()}'\n"
             f"STARTED_AT = '{(as_of + pd.Timedelta(minutes=16)).isoformat()}'\nMODEL_VERSION = None\nCLUSTER_MODEL_VERSION = None\n"
             "MORTGAGE_IDS = None\nBUILD_LIVE_SNAPSHOTS = True\nINJECT_FAILURE = 'after_outputs'\nPUBLISH = True\nPUBLISH_ONLY = False")
    runs = pd.read_parquet(root / "lakehouse" / "gold" / "score_runs.parquet")
    assert list(runs.sort_values("started_at")["status"]) == ["completed", "failed"]
    assert len((root / "eventstream" / "risk_updated.jsonl").read_text(encoding="utf-8").splitlines()) == len(events)

    _run("05_evaluate.ipynb", f"OUTPUT_ROOT = {out}\nMODEL_VERSION = None")
    assert (root / "reports" / "evaluation_report.md").exists()
