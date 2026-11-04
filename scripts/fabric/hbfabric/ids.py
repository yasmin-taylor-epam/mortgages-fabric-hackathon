"""Fixed names and logical IDs of the HomeBridge Fabric items.

A logical ID is the cross-workspace identifier in a `.platform` file. Fabric Git integration and fabric-cicd
use it to bind items to each other (a notebook to its Lakehouse, a pipeline activity to a notebook).
The IDs are derived from the item name, so they stay the same on every run and in every workspace.
"""
from __future__ import annotations

import uuid

NAMESPACE = uuid.UUID("5d0f5f0e-2d7e-4c3a-9b57-3a0f7d1f0c01")  # fixed seed for the derived IDs. Not a secret.
DEFAULT_GUID = "00000000-0000-0000-0000-000000000000"  # fabric-cicd replaces this with the target workspace ID

LAKEHOUSE = "HomeBridgeLakehouse"
ENVIRONMENT = "HomeBridgeEnvironment"
EVENTHOUSE = "HomeBridgeEventhouse"
KQL_DATABASE = "HomeBridgeEvents"
EVENTSTREAM = "HomeBridgeRiskEvents"
PIPELINE_SCORING = "pl_scheduled_scoring"
SEMANTIC_MODEL = "HomeBridgeMortgage"

# Notebooks in run order for the first load. Name = file stem in notebooks/.
NOTEBOOKS = [
    "00_load_raw",
    "01_validate_histories",
    "02_feature_snapshots",
    "03_train_models",
    "04_score_and_segment",
    "05_evaluate",
    "06_publish_delta_tables",
]

NOTEBOOK_DESCRIPTIONS = {
    "00_load_raw": "Loads the synthetic exports into append-only raw tables.",
    "01_validate_histories": "Validates keys and ledger history. Builds clean histories.",
    "02_feature_snapshots": "Builds frozen feature snapshots at each checkpoint or live cutoff.",
    "03_train_models": "Trains the classifier, the rules baseline and the clusters.",
    "04_score_and_segment": "Scores accounts, assigns segments and publishes risk_updated.",
    "05_evaluate": "Later-cohort evaluation report.",
    "06_publish_delta_tables": "Copies the Parquet tables that the semantic model reads into Delta tables.",
}


def logical_id(item_type: str, name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{item_type}/{name}"))
