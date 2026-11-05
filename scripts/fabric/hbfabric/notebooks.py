"""Convert the repository notebooks (.ipynb) to the Fabric Git notebook format (notebook-content.py).

The .ipynb files stay the single source. Local tests run them. This module generates the Fabric items
from them. Format: https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-source-control-deployment
and the notebook definition page of the Fabric REST API (FabricGitSource).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import ids

SPARK_KERNEL = "synapse_pyspark"


def _meta_block(obj: dict) -> list[str]:
    lines = json.dumps(obj, indent=2).splitlines()
    return [f"# META {line}" for line in lines]


def notebook_metadata(lakehouse: bool = True, environment: bool = True) -> dict:
    deps: dict = {}
    if lakehouse:
        lh = ids.logical_id("Lakehouse", ids.LAKEHOUSE)
        deps["lakehouse"] = {
            "default_lakehouse": lh,
            "default_lakehouse_name": ids.LAKEHOUSE,
            "default_lakehouse_workspace_id": ids.DEFAULT_GUID,
            "known_lakehouses": [{"id": lh}],
        }
    if environment:
        deps["environment"] = {"environmentId": ids.logical_id("Environment", ids.ENVIRONMENT), "workspaceId": ids.DEFAULT_GUID}
    return {"kernel_info": {"name": SPARK_KERNEL}, "dependencies": deps}


def _source(cell: dict) -> str:
    src = cell.get("source", "")
    return "".join(src) if isinstance(src, list) else src


def _markdown(text: str) -> list[str]:
    return [("# " + line).rstrip() for line in text.splitlines()]


def ipynb_to_fabric_py(nb: dict) -> str:
    out = ["# Fabric notebook source", "", "# METADATA ********************", ""]
    out += _meta_block(notebook_metadata())
    for cell in nb["cells"]:
        out.append("")
        if cell["cell_type"] == "markdown":
            out += ["# MARKDOWN ********************", ""]
            out += _markdown(_source(cell))
            continue
        tags = cell.get("metadata", {}).get("tags", [])
        out += ["# PARAMETERS CELL ********************" if "parameters" in tags else "# CELL ********************", ""]
        out += _source(cell).rstrip("\n").splitlines()
        out += ["", "# METADATA ********************", ""]
        out += _meta_block({"language": "python", "language_group": SPARK_KERNEL})
    return "\n".join(out) + "\n"


def platform_json(name: str) -> str:
    doc = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {
            "type": "Notebook",
            "displayName": name,
            "description": ids.NOTEBOOK_DESCRIPTIONS[name] + " Synthetic data only.",
        },
        "config": {"version": "2.0", "logicalId": ids.logical_id("Notebook", name)},
    }
    return json.dumps(doc, indent=2) + "\n"


def build_all(notebooks_dir: Path) -> dict[str, str]:
    """Return {relative path under fabric/items: file text} for every notebook."""
    files: dict[str, str] = {}
    for name in ids.NOTEBOOKS:
        nb = json.loads((Path(notebooks_dir) / f"{name}.ipynb").read_text(encoding="utf-8"))
        base = f"{name}.Notebook"
        files[f"{base}/notebook-content.py"] = ipynb_to_fabric_py(nb)
        files[f"{base}/.platform"] = platform_json(name)
    return files
