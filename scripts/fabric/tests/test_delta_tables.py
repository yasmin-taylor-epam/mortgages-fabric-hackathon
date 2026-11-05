"""The Delta tables that notebook 06 writes are exactly the tables that the semantic model reads."""
import json
import re

import pandas as pd
import pytest

from conftest import REPO

MODEL_TABLES = REPO / "reports" / "HomeBridgeMortgage.SemanticModel" / "definition" / "tables"


def _table_sources():
    nb = json.loads((REPO / "notebooks" / "06_publish_delta_tables.ipynb").read_text(encoding="utf-8"))
    cell = next(c for c in nb["cells"] if c["cell_type"] == "code" and "TABLE_SOURCES" in "".join(c["source"]))
    ns: dict = {}
    exec("".join(cell["source"]), ns)  # noqa: S102 - a literal dict from our own notebook
    return ns["TABLE_SOURCES"], ns["GENERATED_TABLES"]


def test_notebook_06_covers_every_model_table():
    entities = {re.search(r"entityName: (\S+)", p.read_text(encoding="utf-8")).group(1) for p in MODEL_TABLES.glob("*.tmdl")}
    sources, generated = _table_sources()
    assert set(sources) | set(generated) == entities
    assert not set(sources) & set(generated)


@pytest.mark.slow
def test_every_source_parquet_exists_after_the_clean_step(tmp_path):
    from hbdata import generator, pipelines_local, validation

    cfg = generator.GenConfig(per_cohort=30, open_cohort_size=20)
    frames, manifest = generator.generate(cfg)
    generator.write_exports(frames, manifest, tmp_path / "exports")
    pipelines_local.load_raw(tmp_path / "exports", tmp_path / "lakehouse", cfg.as_of)
    validation.write_outputs(validation.validate(pipelines_local.read_raw(tmp_path / "lakehouse"), cfg.as_of), tmp_path / "lakehouse")
    sources, _ = _table_sources()
    for table, rel in sources.items():
        if rel.startswith("clean/"):
            assert (tmp_path / "lakehouse" / rel).exists(), f"{rel} is not written by 01_validate_histories"
    mortgage_account = pd.read_parquet(tmp_path / "lakehouse" / "clean" / "mortgage_account.parquet")
    assert "account_key" in mortgage_account.columns
