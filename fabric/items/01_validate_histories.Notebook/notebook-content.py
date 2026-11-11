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

# # 01_validate_histories
#
# **Owner:** Data and replay. **Blueprint step:** 2. **Status:** Runs locally. First ran in Fabric on 5 November 2026.
#
# Validates keys and ledger history in the raw HomeBridge (fictional) tables. It builds clean account histories, report dimensions and feed watermarks. Bad records go to quarantine and stay visible (T02). Reversals keep their audit trail (T04). Stale feeds are flagged (T03).
#
# Input: `lakehouse/raw/` from `pl_load_raw`. Output: `lakehouse/clean/`, `lakehouse/quarantine/`. All data is synthetic.

# PARAMETERS CELL ********************

OUTPUT_ROOT = None  # default: data/output
RUN_AT = None  # default: the generator as_of time

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import sys
from pathlib import Path

# Fabric: the default Lakehouse is mounted at /lakehouse/default. The deploy script uploads the code to
# Files/hb/code (same layout as this repository) and the data to Files/hb/output.
# Local: this notebook sits in notebooks/ and the package in data/.
FABRIC_HB = Path("/lakehouse/default/Files/hb")
if FABRIC_HB.parent.exists():
    DATA_DIR = FABRIC_HB / "code" / "data"
    OUTPUT_ROOT = Path(OUTPUT_ROOT) if OUTPUT_ROOT else FABRIC_HB / "output"
else:
    DATA_DIR = next(p for p in [Path.cwd() / "data", Path.cwd().parent / "data"] if (p / "hbdata").exists())
    OUTPUT_ROOT = Path(OUTPUT_ROOT) if OUTPUT_ROOT else DATA_DIR / "output"
sys.path.insert(0, str(DATA_DIR))
RUN_AT = RUN_AT or None  # a pipeline passes an empty string for no value
LAKEHOUSE = OUTPUT_ROOT / "lakehouse"
import json
import pandas as pd
manifest = json.loads((OUTPUT_ROOT / "exports" / "manifest.json").read_text(encoding="utf-8"))
AS_OF = pd.Timestamp(manifest["config"]["as_of"])
print("Output root:", OUTPUT_ROOT, "| generator as_of:", AS_OF)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from hbdata import pipelines_local, validation

run_at = pd.Timestamp(RUN_AT) if RUN_AT else AS_OF
raw = pipelines_local.read_raw(LAKEHOUSE)
result = validation.validate(raw, run_at)
validation.write_outputs(result, LAKEHOUSE)
pd.DataFrame(result['summary']).T

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Quarantine
#
# Each row keeps the original record as JSON. Nothing here reaches clean history or model inputs.

# CELL ********************

pd.Series(result['quarantine_by_reason'], name='rows').to_frame()

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

result['quarantine'][['dataset', 'record_key', 'mortgage_id', 'reason', 'detail']].head(15)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Feed watermarks (T03)

# CELL ********************

result['watermarks']

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Checks

# CELL ********************

clean = result['clean']
assert clean['payment']['entry_id'].is_unique
assert clean['transfer_request_event']['event_id'].is_unique
assert clean['servicing_contact']['contact_id'].is_unique
q = result['quarantine']
assert not set(q[q.dataset == 'payment'].record_key) & set(clean['payment'].entry_id)
rev = clean['payment'][clean['payment'].entry_type == 'reversal']
assert rev['reversal_of'].isin(clean['payment'].entry_id).all()
print('Checks passed. Clean reversals kept with their original entries:', len(rev))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
