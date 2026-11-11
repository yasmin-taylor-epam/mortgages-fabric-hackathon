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

# # 04_score_and_segment
#
# **Owner:** Models and evaluation. **Blueprint steps:** 4 and 9. **Status:** Runs locally. First ran in Fabric on 5 November 2026.
#
# Scores accounts with pinned model versions, assigns segments and writes Gold tables. Scoring never retrains.
#
# 1. Write a `score_runs` row with status `running`.
# 2. Check the snapshots (INV-2, feature version, run cutoff).
# 3. Append `account_scores`, `feature_attributions` and `segment_membership`. Segment rows are never overwritten.
# 4. Write one `risk_outbox` row per account with a stable `event_id`.
# 5. Mark the run `completed`. Completion is the visibility gate.
# 6. Publish `risk_updated` from the outbox (the publish step of `pl_scheduled_scoring`).
#
# INV-5 / T05: a failed run is marked `failed`. It writes no outbox rows and publishes no `risk_updated` event. The last completed score shows as stale. Set `INJECT_FAILURE` to demonstrate this.
#
# Run kinds: `checkpoint` scores the checkpoint snapshots of open deal cycles. `scheduled` and `manual` score the live snapshots that `02_feature_snapshots` wrote at `CUTOFF`.

# PARAMETERS CELL ********************

OUTPUT_ROOT = None  # default: data/output
RUN_KIND = 'checkpoint'  # 'checkpoint', 'scheduled' or 'manual'
CUTOFF = None  # ISO 8601. Default: the generator as_of time
STARTED_AT = None  # simulated run start (ISO 8601). Default: now, or one minute after HB_AS_OF when it is set
MODEL_VERSION = None  # pinned classifier, for example 'hb-refi-clf:1'. Default: latest release
CLUSTER_MODEL_VERSION = None  # pinned cluster model. Default: same release
MORTGAGE_IDS = None  # optional list of mortgage_ids
BUILD_LIVE_SNAPSHOTS = False  # local convenience: build live snapshots here instead of in 02_feature_snapshots
INJECT_FAILURE = None  # T05 demo: 'before_outputs' or 'after_outputs'
PUBLISH = True  # publish risk_updated after a completed run
PUBLISH_ONLY = False  # retry pending publication without scoring
ALERT_THRESHOLD = None  # default: the release's alert threshold, chosen by its named rule. The routing policy copies the same value

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
CUTOFF, STARTED_AT, MODEL_VERSION = CUTOFF or None, STARTED_AT or None, MODEL_VERSION or None  # a pipeline passes empty strings
CLUSTER_MODEL_VERSION, INJECT_FAILURE = CLUSTER_MODEL_VERSION or None, INJECT_FAILURE or None
ALERT_THRESHOLD = globals().get('ALERT_THRESHOLD') or None  # a caller may omit it. A pipeline passes an empty string
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

from models_eval import asof, pipeline, publish, scoring
result = None
if not PUBLISH_ONLY:
    if CUTOFF is None:  # local default: the generator as_of time
        manifest = json.loads((PATHS.root / 'exports' / 'manifest.json').read_text(encoding='utf-8'))
        CUTOFF = manifest['config']['as_of']
    cutoff = pd.Timestamp(CUTOFF)
    if BUILD_LIVE_SNAPSHOTS and RUN_KIND != 'checkpoint':
        pipeline.live_snapshots(PATHS, cutoff, MORTGAGE_IDS)
    snap = pipeline.select_snapshots(PATHS, cutoff, RUN_KIND, MORTGAGE_IDS)
    print(len(snap), 'snapshots at cutoff', cutoff)
    # A failure raises ScoringRunFailed. The pipeline activity then fails and the publish step does not run.
    result = scoring.run_scoring(PATHS, snap, RUN_KIND, cutoff, MODEL_VERSION, CLUSTER_MODEL_VERSION,
                                 started_at=STARTED_AT, inject_failure=INJECT_FAILURE,
                                 alert_threshold=float(ALERT_THRESHOLD) if ALERT_THRESHOLD else None)
result

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

publication = publish.publish_pending(PATHS, now=STARTED_AT) if (PUBLISH or PUBLISH_ONLY) else 'skipped'
publication

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Run status
#
# Only completed runs are visible to the report and the case writer.

# CELL ********************

from models_eval import gold
gold.read(PATHS.gold, 'score_runs').sort_values('started_at').tail(5)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Latest score per account
#
# The Data and replay `score_context` rule: a score is stale when a later run failed (T05) or the score is old.

# CELL ********************

as_of = pd.Timestamp(STARTED_AT) if STARTED_AT else asof.now()
latest = scoring.latest_scores(PATHS, as_of)
latest['score_state'].value_counts()

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Ten highest scores in this run
#
# The proposed capacity is ten reviews per run. Attributions explain the score. They do not show the borrower's motive. Payment-support routing (INV-1) does not depend on these scores.

# CELL ********************

if result:
    scores = gold.read(PATHS.gold, 'account_scores')
    top = scores[scores['run_id'] == result['run_id']].nlargest(10, 'score')
    attr = gold.read(PATHS.gold, 'feature_attributions')
    attr = attr[(attr['run_id'] == result['run_id']) & (attr['rank'] <= 3)]
    drivers = attr.groupby('account_key').apply(lambda d: '; '.join(f"{f} ({c:+.2f})" for f, c in zip(d['feature_name'], d['contribution'])), include_groups=False)
    display(top[['account_key', 'score', 'segment_id', 'snapshot_id']].assign(top_drivers=top['account_key'].map(drivers)))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Segment counts for this run
#
# Each run appends one row per account. Earlier rows never change.

# CELL ********************

if result:
    seg = gold.read(PATHS.gold, 'segment_membership')
    display(seg[seg['run_id'] == result['run_id']]['segment_id'].value_counts().sort_index())

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
