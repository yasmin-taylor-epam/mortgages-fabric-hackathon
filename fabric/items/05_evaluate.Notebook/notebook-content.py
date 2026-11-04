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

# # 05_evaluate
#
# **Owner:** Models and evaluation. **Blueprint:** section 04, Evaluation. **Status:** Runs locally. Not yet run in Fabric.
#
# Tests each model on the later deal cohorts. Their borrowers are not in training. Thresholds come from the release.
#
# Reports accuracy, precision, recall, calibration and lift at ten reviews per run, and compares each model with the rules baseline. Lift compares the refinancing rate in the ten highest-scored accounts with the cohort base rate.
#
# **Synthetic data.** HomeBridge is fictional. These results show that the workflow works. They do not show real predictive power or improved retention.
#
# Output: `reports/evaluation_report.md` and `.json` under the output root.

# PARAMETERS CELL ********************

OUTPUT_ROOT = None  # default: data/output
MODEL_VERSION = None  # default: latest release

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
# Files/hb/code (same layout as this repository) and the data to Files/hb/output. Not yet run in Fabric.
# Local: this notebook sits in notebooks/ and imports notebooks/models_eval.
FABRIC_HB = Path('/lakehouse/default/Files/hb')
if FABRIC_HB.parent.exists():
    NOTEBOOKS_DIR = FABRIC_HB / 'code' / 'notebooks'
    OUTPUT_ROOT = OUTPUT_ROOT or str(FABRIC_HB / 'output')
    os.environ.setdefault('HB_MLFLOW_URI', 'fabric')  # use the workspace MLflow tracking server
else:
    NOTEBOOKS_DIR = next(p for p in [Path.cwd(), Path.cwd() / 'notebooks'] if (p / 'models_eval').exists())
sys.path.insert(0, str(NOTEBOOKS_DIR))
MODEL_VERSION = MODEL_VERSION or None  # a pipeline passes an empty string for no value
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

from models_eval import evaluate
report = evaluate.evaluate_release(PATHS, MODEL_VERSION)
files = evaluate.write_report(PATHS, report)
files

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Model comparison on the test cohorts

# CELL ********************

cols = ['role', 'threshold', 'accuracy', 'majority_class_accuracy', 'precision', 'recall', 'roc_auc', 'average_precision', 'mean_lift_at_10']
pd.DataFrame(report['models']).T[cols]

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Lift at ten reviews per run
#
# One checkpoint run scores one deal-end cohort. Ten accounts is a small sample, so the bootstrap interval is wide.

# CELL ********************

pd.DataFrame([{'model': m, **c} for m, d in report['models'].items() for c in d['lift_at_10_by_cohort']])

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Calibration

# CELL ********************

pd.DataFrame([{'model': m, **b} for m, d in report['models'].items() for b in d['calibration']['bins']]).pivot(index='bin', columns='model', values=['mean_score', 'observed_rate']).round(3)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Segments on the test cohorts

# CELL ********************

pd.DataFrame(report['segments']['test_profiles'])

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Proxy check
#
# Correlation of model inputs with context that the model does not use.

# CELL ********************

pd.DataFrame(report['proxy_check']['results'])

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Full report

# CELL ********************

from IPython.display import Markdown
Markdown(evaluate.to_markdown(report))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
