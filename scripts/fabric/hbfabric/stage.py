"""Build the staging folder that fabric-cicd deploys, and check it offline.

The staging folder holds every item folder side by side:
- fabric/items/*                      notebooks, Lakehouse, Environment, Eventhouse, KQL database, Eventstream, pipeline
- reports/HomeBridgeMortgage.*        semantic model and report (they stay where the Dashboard and action package keeps them)

This is also the layout to copy into a Git-connected Fabric workspace folder (for example the team repository).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import ids, kql

REPO = Path(__file__).resolve().parents[3]
ITEMS_DIR = REPO / "fabric" / "items"
EXTRA_ITEM_DIRS = [
    REPO / "reports" / "HomeBridgeMortgage.SemanticModel",
    REPO / "reports" / "HomeBridgeMortgage.Report",
]
# Copied from fabric-cicd SERIAL_ITEM_PUBLISH_ORDER (v1.4.0). A logical ID in an item file can only
# point to an item type that fabric-cicd publishes earlier.
PUBLISH_ORDER = {
    "Lakehouse": 4, "Environment": 7, "Eventhouse": 9, "Notebook": 11, "SemanticModel": 12, "Report": 13,
    "KQLDatabase": 17, "DataPipeline": 20, "Reflex": 21, "Eventstream": 22,
}
CORE_TYPES = ["Lakehouse", "Environment", "Eventhouse", "KQLDatabase", "Notebook", "DataPipeline", "Eventstream"]
MODEL_TYPES = ["Lakehouse", "SemanticModel", "Report"]  # Lakehouse stays in scope so that $items.Lakehouse.* resolves
REQUIRED_FILES = {
    "Lakehouse": [".platform"],
    "Environment": [".platform", "Libraries/PublicLibraries/environment.yml"],
    "Eventhouse": [".platform", "EventhouseProperties.json"],
    "KQLDatabase": [".platform", "DatabaseProperties.json", "DatabaseSchema.kql"],
    "Notebook": [".platform", "notebook-content.py"],
    "DataPipeline": [".platform", "pipeline-content.json"],
    "Eventstream": [".platform", "eventstream.json"],
    "SemanticModel": [".platform", "definition.pbism", "definition/database.tmdl"],
    "Report": [".platform", "definition.pbir", "definition/report.json"],
}
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
SECRET_PATTERNS = [re.compile(p, re.I) for p in (r"SharedAccessKey=", r"Endpoint=sb://", r"AccountKey=", r"Bearer\s+ey", r"client_secret")]
PLACEHOLDERS = ("REPLACE-WITH-SQL-ANALYTICS-ENDPOINT", "REPLACE-WITH-LAKEHOUSE-ID")
REFERENCE_KEYS = {  # key in a file -> item type that the GUID must belong to
    "default_lakehouse": "Lakehouse",
    "environmentId": "Environment",
    "notebookId": "Notebook",
    "parentEventhouseItemId": "Eventhouse",
}


def item_dirs(root: Path) -> list[Path]:
    """Every folder that holds a .platform file, found the way fabric-cicd finds them."""
    return sorted(p.parent for p in Path(root).rglob(".platform"))


def build(stage_dir: Path, items_dir: Path = ITEMS_DIR, extra: list[Path] | None = None) -> list[Path]:
    stage_dir = Path(stage_dir)
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    out = []
    sources = [p for p in sorted(Path(items_dir).iterdir()) if p.is_dir()] + list(EXTRA_ITEM_DIRS if extra is None else extra)
    for src in sources:
        dest = stage_dir / src.name
        shutil.copytree(src, dest)
        out.append(dest)
    return out


def _meta_json(text: str) -> list[dict]:
    """Parse the '# META' JSON blocks of a Fabric notebook source file."""
    blocks, cur = [], []
    for line in text.splitlines():
        if line == "# META" or line.startswith("# META "):
            cur.append(line[len("# META "):])
        elif cur:
            blocks.append(json.loads("\n".join(cur)))
            cur = []
    if cur:
        blocks.append(json.loads("\n".join(cur)))
    return blocks


def validate(stage_dir: Path) -> list[str]:
    """Return a list of problems. An empty list means the offline checks passed."""
    stage_dir = Path(stage_dir)
    problems: list[str] = []
    logical: dict[str, tuple[str, str]] = {}
    for d in item_dirs(stage_dir):
        rel = d.relative_to(stage_dir).as_posix()
        try:
            plat = json.loads((d / ".platform").read_text(encoding="utf-8"))
            item_type, name = plat["metadata"]["type"], plat["metadata"]["displayName"]
            lid = plat["config"]["logicalId"]
        except (ValueError, KeyError) as exc:
            problems.append(f"{rel}: bad .platform ({exc})")
            continue
        if d.name != f"{name}.{item_type}":
            problems.append(f"{rel}: folder name must be '{name}.{item_type}'")
        if not GUID.fullmatch(lid) or lid == ids.DEFAULT_GUID:
            problems.append(f"{rel}: logicalId must be a unique GUID, not the default")
        elif lid in logical:
            problems.append(f"{rel}: duplicate logicalId, also used by {logical[lid][1]}")
        logical[lid] = (item_type, rel)
        for req in REQUIRED_FILES.get(item_type, [".platform"]):
            if not (d / req).exists():
                problems.append(f"{rel}: missing {req}")
        if item_type not in PUBLISH_ORDER:
            problems.append(f"{rel}: item type {item_type} is not covered by the offline checks")

    for d in item_dirs(stage_dir):
        rel = d.relative_to(stage_dir).as_posix()
        plat = json.loads((d / ".platform").read_text(encoding="utf-8"))
        item_type = plat["metadata"]["type"]
        for f in d.rglob("*"):
            if not f.is_file() or f.suffix in {".parquet", ".whl"} or ".children" in f.relative_to(d).parts:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            for pat in SECRET_PATTERNS:
                if pat.search(text):
                    problems.append(f"{rel}/{f.relative_to(d).as_posix()}: looks like a secret ({pat.pattern})")
            if f.name in {"notebook-content.py", "pipeline-content.json", "eventstream.json", "DatabaseProperties.json"}:
                problems += _check_references(rel, f, text, item_type, logical)
        if item_type == "Notebook":
            problems += _check_notebook(rel, d / "notebook-content.py")
        if item_type == "KQLDatabase":
            body = (d / "DatabaseSchema.kql").read_text(encoding="utf-8")
            for line in body.splitlines():
                if line.startswith(".") and not any(p.match(line) for p in kql.SUPPORTED):
                    problems.append(f"{rel}/DatabaseSchema.kql: unsupported command: {line[:60]}")
        if item_type == "SemanticModel":
            expr = (d / "definition" / "expressions.tmdl").read_text(encoding="utf-8")
            for ph in PLACEHOLDERS:
                if ph not in expr:
                    problems.append(f"{rel}: placeholder {ph} not found. The deploy parameter file replaces it.")
    return problems


def _check_references(rel: str, f: Path, text: str, item_type: str, logical: dict) -> list[str]:
    out = []
    own = f"{rel}/{f.name}"
    for key, want in REFERENCE_KEYS.items():
        for m in re.finditer(rf'"{key}"\s*:\s*"({GUID.pattern})"', text):
            guid = m.group(1)
            hit = logical.get(guid)
            if not hit or hit[0] != want:
                out.append(f"{own}: {key} {guid} is not the logicalId of a {want} item in this folder")
            elif PUBLISH_ORDER.get(hit[0], 0) >= PUBLISH_ORDER.get(item_type, 99):
                out.append(f"{own}: {hit[0]} is published after {item_type}, so fabric-cicd cannot resolve {key}")
    for m in re.finditer(r'"itemId"\s*:\s*"(' + GUID.pattern + ')"', text):
        hit = logical.get(m.group(1))
        if not hit or hit[0] not in {"KQLDatabase", "Lakehouse"}:
            out.append(f"{own}: itemId {m.group(1)} is not the logicalId of a KQL database or Lakehouse")
        elif PUBLISH_ORDER[hit[0]] >= PUBLISH_ORDER.get(item_type, 99):
            out.append(f"{own}: {hit[0]} is published after {item_type}")
    return out


def _check_notebook(rel: str, path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    out = []
    if not text.startswith("# Fabric notebook source"):
        out.append(f"{rel}: notebook-content.py must start with '# Fabric notebook source'")
    try:
        blocks = _meta_json(text)
    except ValueError as exc:
        return out + [f"{rel}: a '# META' block is not valid JSON ({exc})"]
    deps = blocks[0].get("dependencies", {}) if blocks else {}
    if "default_lakehouse" not in deps.get("lakehouse", {}):
        out.append(f"{rel}: no default Lakehouse in the notebook header")
    if text.count("# PARAMETERS CELL") != 1:
        out.append(f"{rel}: expected exactly one parameters cell")
    return out
