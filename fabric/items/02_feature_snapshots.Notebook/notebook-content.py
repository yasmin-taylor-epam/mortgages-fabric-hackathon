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

# # 02_feature_snapshots
#
# **Owner:** Data and replay. **Blueprint step:** 2. **Status:** Runs locally. First ran in Fabric on 5 November 2026.
#
# Builds frozen feature snapshots. A checkpoint snapshot is taken 90 days before each deal end. A live snapshot is taken at a chosen cutoff and uses staged journey results from `pl_export_journeys`.
#
# - INV-2: every input has its event time and its ingestion time at or before `cutoff_at`.
# - A snapshot is immutable per `snapshot_id`. Rewriting one with different content fails.
# - Observed outcomes are written to a separate table. They never enter a snapshot.
#
# Output: `lakehouse/features/feature_snapshot.parquet` and `lakehouse/features/observed_outcome.parquet`. All data is synthetic.

# PARAMETERS CELL ********************

OUTPUT_ROOT = None  # default: data/output
MODE = 'checkpoint'  # 'checkpoint' or 'live'
CUTOFF = None  # live mode: cutoff time (ISO 8601)
MORTGAGE_IDS = None  # live mode: optional list of mortgage_ids

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
CUTOFF = CUTOFF or None  # a pipeline passes an empty string for no value
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

from hbdata import io, pipelines_local, snapshots

clean = pipelines_local.read_clean(LAKEHOUSE)
if MODE == 'live':
    cutoff = pd.Timestamp(CUTOFF)
    requests = snapshots.live_requests(clean['mortgage_account'], cutoff, MORTGAGE_IDS)
    staged = pipelines_local.read_staged_events(LAKEHOUSE)
    created_at = cutoff
else:
    requests = snapshots.checkpoint_requests(clean['mortgage_account'], AS_OF)
    staged = None
    created_at = AS_OF
snap = snapshots.build_snapshots(clean, requests, created_at, staged)
stats = snapshots.append_snapshots(snap, LAKEHOUSE / 'features' / 'feature_snapshot.parquet')
io.write_table(clean['observed_outcome'], LAKEHOUSE / 'features' / 'observed_outcome.parquet', 'observed_outcome')
stats

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## INV-2 checks
#
# Every source watermark in a snapshot must be at or before its cutoff.

# CELL ********************

wm = snap['source_watermark'].map(json.loads)
for name in snapshots.SOURCES:
    t = pd.to_datetime(wm.map(lambda d: d[name]), utc=True, format="ISO8601")
    assert (t.isna() | (t <= snap['cutoff_at'])).all(), name
assert not [c for c in snap.columns if 'outcome' in c or c == 'label']
print('INV-2 watermark checks passed for', len(snap), 'snapshots')

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Summary by deal-end cohort

# CELL ********************

snap.groupby('cohort_id').agg(snapshots=('snapshot_id', 'count'), started=('pt_request_started', 'mean'),
    software_faults=('pt_software_errors_90d', 'mean'), unresolved_complaint=('svc_unresolved_complaint', 'mean'),
    payment_feed_stale=('ctx_payment_feed_stale', 'sum')).round(3)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Illustrative personas
#
# Alex, Sam, Casey and Robin are fictional demo personas (see `data/README.md`).

# CELL ********************

cols = ['mortgage_id', 'snapshot_kind', 'cutoff_at', 'pt_latest_stage', 'pt_software_errors_90d', 'pt_unrecovered_software_error',
        'svc_contacts_90d', 'svc_max_contacts_per_case_365d', 'svc_repeat_contacts_unresolved', 'svc_unresolved_complaint', 'ctx_reversals_180d']
snap[snap['mortgage_id'].str.startswith('HB-M-9000')][cols]

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
