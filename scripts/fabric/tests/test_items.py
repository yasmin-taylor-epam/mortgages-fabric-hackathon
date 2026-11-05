"""The committed Fabric items match their sources, and have the shape that Fabric Git integration expects."""
import json
import re
import subprocess
import sys

import build_fabric_items as bfi
from hbfabric import ids, kql, notebooks, stage


def test_generated_files_are_up_to_date():
    files, dropped = bfi.generated_files()
    for rel, text in files.items():
        assert (bfi.ITEMS / rel).read_text(encoding="utf-8") == text, f"{rel} is out of date. Run scripts/fabric/build_fabric_items.py"
    assert dropped == bfi.EXPECTED_DROPPED


def test_check_mode_exit_code():
    out = subprocess.run([sys.executable, str(bfi.REPO / "scripts" / "fabric" / "build_fabric_items.py"), "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_every_notebook_has_one_parameters_cell_and_the_lakehouse_binding():
    for name in ids.NOTEBOOKS:
        text = (bfi.ITEMS / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")
        assert text.startswith("# Fabric notebook source")
        assert text.count("# PARAMETERS CELL ********************") == 1
        meta = stage._meta_json(text)[0]
        lh = meta["dependencies"]["lakehouse"]
        assert lh["default_lakehouse"] == ids.logical_id("Lakehouse", ids.LAKEHOUSE)
        assert lh["default_lakehouse_workspace_id"] == ids.DEFAULT_GUID
        assert meta["dependencies"]["environment"]["environmentId"] == ids.logical_id("Environment", ids.ENVIRONMENT)


def test_notebook_conversion_keeps_every_code_cell_verbatim():
    for name in ids.NOTEBOOKS:
        nb = json.loads((bfi.REPO / "notebooks" / f"{name}.ipynb").read_text(encoding="utf-8"))
        text = (bfi.ITEMS / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")
        for cell in nb["cells"]:
            if cell["cell_type"] == "code":
                assert "".join(cell["source"]).strip() in text


def test_logical_ids_in_platform_files_match_the_names():
    for d in stage.item_dirs(bfi.ITEMS):
        plat = json.loads((d / ".platform").read_text(encoding="utf-8"))
        meta = plat["metadata"]
        assert plat["config"]["logicalId"] == ids.logical_id(meta["type"], meta["displayName"]), d.name


def test_database_schema_holds_only_supported_commands_and_all_tables():
    text = (bfi.ITEMS / bfi.KQL_SCHEMA).read_text(encoding="utf-8")
    commands = [l for l in text.splitlines() if l.startswith(".")]
    assert commands and all(any(p.match(c) for p in kql.SUPPORTED) for c in commands)
    assert "execute database script" not in text
    for table in ("RawEventsLanding", "RawEvents", "QuarantinedEvents", "AccountContext", "ScoreContext", "SourceWatermarks"):
        assert re.search(rf"^\.create-merge table {table}\b", text, re.M)
    assert "TransferEventsAt" in text and "Customer360" in text


def test_pipeline_points_to_the_two_notebooks_by_logical_id():
    doc = json.loads((bfi.ITEMS / "pl_scheduled_scoring.DataPipeline" / "pipeline-content.json").read_text(encoding="utf-8"))
    acts = {a["name"]: a for a in doc["properties"]["activities"]}
    assert acts["Build snapshots (02_feature_snapshots)"]["typeProperties"]["notebookId"] == ids.logical_id("Notebook", "02_feature_snapshots")
    assert acts["Score and segment (04_score_and_segment)"]["typeProperties"]["notebookId"] == ids.logical_id("Notebook", "04_score_and_segment")
    publish = acts["Publish risk_updated (04_score_and_segment, publish only)"]
    assert publish["dependsOn"][0]["dependencyConditions"] == ["Succeeded"]  # INV-5: no publish after a failed run
    assert acts["Score and segment (04_score_and_segment)"]["policy"]["retry"] == 0
    assert all(a["typeProperties"].get("workspaceId", ids.DEFAULT_GUID) == ids.DEFAULT_GUID for a in acts.values())


def test_eventstream_routes_to_the_landing_table_of_the_kql_database():
    doc = json.loads((bfi.ITEMS / "HomeBridgeRiskEvents.Eventstream" / "eventstream.json").read_text(encoding="utf-8"))
    dest = doc["destinations"][0]["properties"]
    assert dest["itemId"] == ids.logical_id("KQLDatabase", ids.KQL_DATABASE)
    assert dest["tableName"] == "RawEventsLanding" and dest["databaseName"] == ids.KQL_DATABASE
    assert doc["sources"][0]["type"] == "CustomEndpoint"
