# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "7c9137fd-b0e2-58fb-b1c4-dc7c07a38178",
# META       "default_lakehouse_name": "HomeBridgeLakehouse",
# META       "default_lakehouse_workspace_id": "00000000-0000-0000-0000-000000000000",
# META       "known_lakehouses": [
# META         {
# META           "id": "7c9137fd-b0e2-58fb-b1c4-dc7c07a38178"
# META         }
# META       ]
# META     },
# META     "environment": {
# META       "environmentId": "966d2136-aa45-5f03-8f16-91f83e9bdef5",
# META       "workspaceId": "00000000-0000-0000-0000-000000000000"
# META     }
# META   }
# META }

# MARKDOWN ********************

# # 03_train_models
#
# **Owner:** Models and evaluation. **Blueprint step:** 3. **Status:** Runs locally. First ran in Fabric on 5 November 2026.
#
# Trains one model release for the fictional lender HomeBridge. Synthetic data only.
#
# - Rules baseline: product-transfer request friction.
# - Logistic regression and a shallow decision tree (depth 3 or 4).
# - Two to five behavioural clusters, fitted once per release.
#
# Rules:
#
# - INV-2: snapshots must hold only data known at their cutoff. The run stops otherwise.
# - INV-4: only labelled outcomes train the model. Unknown, pending and excluded outcomes never count as retained.
# - Training and validation labels must be observed before the simulated release date. Test cohorts end later. Their borrowers stay out of training.
# - Hyperparameters and alert thresholds are frozen on validation data.
#
# Inputs: `lakehouse/features/feature_snapshot` and `observed_outcome` from Data and replay. Output: an MLflow run, registered models `hb-refi-clf` and `hb-segments`, and a release folder.

# PARAMETERS CELL ********************

OUTPUT_ROOT = None  # default: data/output
USE_MLFLOW = True
N_BOOT = 20  # bootstrap fits per k for cluster stability
THRESHOLD_RULE = None  # empty: the default rule, min_precision. Others: max_f1, min_cost (needs HB_COST_MISSED_LEAVER and HB_COST_REVIEW)
MIN_PRECISION = None  # min_precision rule: share of validation alerts that must be real leavers. Empty: 0.6

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import os
import sys
from pathlib import Path

# Fabric: the default Lakehouse is mounted at /lakehouse/default. The deploy script uploads the code to
# Files/hb/code (same layout as this repository) and the data to Files/hb/output.
# Local: this notebook sits in notebooks/ and imports notebooks/models_eval.
FABRIC_HB = Path('/lakehouse/default/Files/hb')
if FABRIC_HB.parent.exists():
    NOTEBOOKS_DIR = FABRIC_HB / 'code' / 'notebooks'
    OUTPUT_ROOT = OUTPUT_ROOT or str(FABRIC_HB / 'output')
    os.environ.setdefault('HB_MLFLOW_URI', 'fabric')  # use the workspace MLflow tracking server
else:
    NOTEBOOKS_DIR = next(p for p in [Path.cwd(), Path.cwd() / 'notebooks'] if (p / 'models_eval').exists())
sys.path.insert(0, str(NOTEBOOKS_DIR))
import json
import pandas as pd
from models_eval import config
PATHS = config.paths(OUTPUT_ROOT)
print('Output root:', PATHS.root)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from models_eval import config, train
rule = config.threshold_rule(THRESHOLD_RULE or None, None, None, MIN_PRECISION or None)  # a pipeline passes empty strings
release = train.train_release(PATHS, use_mlflow=USE_MLFLOW, n_boot=N_BOOT, rule=rule)
{k: release[k] for k in ('release_id', 'champion', 'model_version', 'cluster_model_version', 'alert_threshold', 'alert_threshold_rule', 'alert_threshold_params', 'registry')}

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Split
#
# Rows per split after the release-date rule and the shared-borrower rule.

# CELL ********************

pd.DataFrame({k: release['split'][k] for k in ('rows', 'positive_rate', 'dropped_after_release', 'dropped_shared_borrower')})

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Validation results
#
# The champion is the learned model with the higher validation average precision. Logistic regression wins a near tie. The routing threshold comes from a named rule on validation data. The default rule is `min_precision` with a target of 0.6: the lowest threshold at which at least 60% of the validation alerts are real leavers. The release stores the rule and the value. The team confirms the rule.

# CELL ********************

pd.DataFrame(release['validation']).T

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

pd.DataFrame(release['params']).T

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Segments
#
# k is the best silhouette among stable solutions (mean bootstrap adjusted Rand index of at least 0.8, worst of at least 0.5). Segment names follow a human inspection of these profiles.

# CELL ********************

pd.DataFrame(release['segments']['selection'])

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

pd.DataFrame(release['segments']['profiles'])

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
