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

# # 00_load_raw
#
# **Owner:** Data and replay (stand-in for the `pl_load_raw` pipeline). **Blueprint step:** 1. **Status:** Runs locally. First ran in Fabric on 5 November 2026.
#
# Loads the dated synthetic exports into append-only raw tables. A load never overwrites an earlier load. A file that was already loaded (same hash) is skipped.
#
# Input: `exports/*.parquet` and `exports/manifest.json` under the output root. In Fabric, the deploy script uploads them to `Files/hb/output/exports/`. Output: `lakehouse/raw/`. All data is synthetic.
#
# This notebook does the same work as `python -m hbdata load-raw`. Replace it with the real `pl_load_raw` pipeline when that is built.

# PARAMETERS CELL ********************

OUTPUT_ROOT = None  # default: data/output
LOADED_AT = None  # load time (ISO 8601). Default: the generator as_of time

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
LOADED_AT = LOADED_AT or None  # a pipeline passes an empty string for no value
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

from hbdata import pipelines_local

entry = pipelines_local.load_raw(OUTPUT_ROOT / "exports", LAKEHOUSE, pd.Timestamp(LOADED_AT) if LOADED_AT else AS_OF)
pd.DataFrame(entry["files"]).T

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Checks
#
# Every dataset must have a raw file after the load.

# CELL ********************

raw = pipelines_local.read_raw(LAKEHOUSE)
empty = [name for name, df in raw.items() if len(df) == 0]
assert not empty, f"No raw rows for: {empty}"
print("Raw rows per dataset:", {name: len(df) for name, df in raw.items()})

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
