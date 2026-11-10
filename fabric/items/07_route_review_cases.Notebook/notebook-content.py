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

# # 07_route_review_cases
#
# **Owner:** Dashboard and action (demo portals). **Blueprint step:** 7 (route alerts to a named owner). **Status:** Runs locally through its helper tests. It ran in the Fabric workspace azure-mafia-branch-luke on 10 Nov 2026, before the open-faults rule was added.
#
# Turns scores and service events into review cases, and writes them to the Lakehouse table `review_cases`. The staff portal and the Power BI model read that table.
#
# - The routing rules are not in this notebook. They are the action package (`action/homebridge_action`): the router, the idempotent case writer and the routing policy. The helper `notebooks/review_routing` only collects the inputs.
# - Inputs: the account context (built from the clean Lakehouse files), the published `risk_outbox` rows of the latest completed scoring run, the run statuses in `score_runs`, and the service events from the KQL function `ServiceEvents()` in the Eventhouse.
# - Open faults only: a verified portal fault opens a technical case only if it is still unresolved at `AS_OF` (the demo clock). A fault is left out when the customer went on with the same application later, when the application is closed (completed, abandoned or rejected), or when the deal ended. The renewal steps come from `RawEvents`. Left-out faults get a decision row (`no_case`) with the reason.
# - `EVENTS_BEFORE` builds a baseline: only service events and renewal steps that happened before that time are routed. Leave it empty in the demo, so a live event opens its case.
# - Each event is routed at its own arrival time. A rerun gives the same cases. One case per `case_key` (mortgage, deal cycle and case family), so a repeated event does not create a second case.
# - `review_cases` is updated with a MERGE by `case_key`. Rows are never deleted. Missing columns are added. `review_decisions` holds the decision for each event of the latest run. `review_routing_runs` gets one summary row per run.
#
# The SharePoint list and Power Automate flow are not built. This Lakehouse table stands in for them. All data is synthetic.

# PARAMETERS CELL ********************

OUTPUT_ROOT_REL = 'Files/hb/output'  # relative to the default Lakehouse
CONTEXT_AS_OF = '2026-10-15T00:00:00Z'  # data as-of time for the account context. The synthetic data ends here
KUSTO_URI = ''  # query URI of the HomeBridge Eventhouse. Empty: route scores only, with no service events
KUSTO_DB = ''  # KQL database name (the item ID in Fabric)
POLICY_REL = 'action/policy/routing-policy.candidate.json'  # under the uploaded code folder. With no risk_threshold, see below
AS_OF = '2026-10-16T09:00:00Z'  # the demo clock. A portal fault opens a case only if it is still unresolved at this time
EVENTS_BEFORE = ''  # empty: route every event. A time such as 2026-10-16T09:00:00Z: route only events that happened before it

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HB = Path('/lakehouse/default/Files/hb')
if not HB.exists():
    raise RuntimeError('Attach the HomeBridge Lakehouse as the default Lakehouse. This notebook runs only in Fabric. '
                       'Its logic is tested locally in notebooks/review_routing/tests.')
CODE = HB / 'code'
for p in (CODE / 'notebooks', CODE / 'action', CODE / 'data'):
    sys.path.insert(0, str(p))

from hbdata import io, pipelines_local  # noqa: E402
from homebridge_action.models import RoutingPolicy  # noqa: E402
from review_routing import route as rr  # noqa: E402

LAKEHOUSE = Path('/lakehouse/default') / OUTPUT_ROOT_REL / 'lakehouse'
# A policy file with no threshold is a template. Then the threshold comes from the model release (see rr.load_policy)
POLICY_DOC, POLICY_SOURCE = rr.load_policy(CODE / POLICY_REL, Path('/lakehouse/default') / OUTPUT_ROOT_REL)
POLICY = RoutingPolicy.from_dict(POLICY_DOC)
ROUTED_AT = datetime.now(timezone.utc)
print('Policy:', POLICY.version, 'from', POLICY_SOURCE, '| threshold:', POLICY.risk_threshold, '| routed at:', ROUTED_AT.isoformat())
print('As of:', AS_OF, '| events before:', EVENTS_BEFORE or '(all events)')

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Inputs

# CELL ********************

clean = pipelines_local.read_clean(LAKEHOUSE)
report = json.loads((LAKEHOUSE / 'clean' / '_validation_report.json').read_text(encoding='utf-8'))
watermarks = io.read_table(LAKEHOUSE / 'clean' / 'feed_watermark.parquet')
context = pipelines_local.account_context(clean, report['conflict_mortgages'], watermarks, CONTEXT_AS_OF)

score_runs = pd.read_parquet(LAKEHOUSE / 'gold' / 'score_runs.parquet')
outbox = pd.read_parquet(LAKEHOUSE / 'gold' / 'risk_outbox.parquet')
RUN_ID = rr.latest_completed_run(score_runs)
risk = rr.risk_candidates(outbox, RUN_ID)
print(len(context), 'accounts in context |', len(risk), 'risk_updated events from run', RUN_ID)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import requests

def kql_rows(query: str) -> list[dict]:
    try:
        token = notebookutils.credentials.getToken(KUSTO_URI)  # noqa: F821 (Fabric built-in)
    except Exception:
        token = notebookutils.credentials.getToken('kusto')  # noqa: F821
    resp = requests.post(f"{KUSTO_URI.rstrip('/')}/v1/rest/query", timeout=120,
                         headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                         json={'db': KUSTO_DB, 'csl': query})
    resp.raise_for_status()
    table = resp.json()['Tables'][0]  # v1 response: the first table is the query result
    names = [c['ColumnName'] for c in table['Columns']]
    return [dict(zip(names, row)) for row in table['Rows']]

# Renewal steps of the applications that had a verified fault, once per event_id (a resend is a copy)
STEPS_KQL = '''let fault_apps = RawEvents
    | where event_type == 'transfer_request_event' and tostring(payload.error_class) == 'software_fault' and tobool(payload.fault_verified)
    | distinct application_id;
RawEvents
| where event_type == 'transfer_request_event' and application_id in (fault_apps)
| summarize arg_max(ingested_at, application_id, occurred_at, payload) by event_id
| project application_id, occurred_at, status = tostring(payload.status)'''

live = bool(KUSTO_URI and KUSTO_DB)
service_rows = rr.before(kql_rows('ServiceEvents()'), EVENTS_BEFORE) if live else []
steps = rr.transfer_steps(rr.before(kql_rows(STEPS_KQL), EVENTS_BEFORE)) if live else rr.transfer_steps([])
service_rows, LEFT_OUT = rr.open_faults(service_rows, steps, rr.deal_ends(context), AS_OF)
service = rr.service_candidates(service_rows)
print(len(service), 'service events routed' if live else 'service events (KUSTO_URI not set)', '|',
      len(LEFT_OUT), 'resolved portal faults left out |', len(steps), 'renewal steps read')

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Route with the action package

# CELL ********************

result = rr.route(rr.contexts(context), risk + service, dict(zip(score_runs['run_id'], score_runs['status'])),
                  POLICY, ROUTED_AT, LEFT_OUT)
SUMMARY = rr.summary(result, RUN_ID, len(service), ROUTED_AT)
SUMMARY.update({'as_of': AS_OF, 'events_before': EVENTS_BEFORE or None, 'faults_left_out': len(LEFT_OUT)})
SUMMARY

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Write to the Lakehouse

# CELL ********************

from pyspark.sql import types as T

CASE_SCHEMA = T.StructType([
    T.StructField(c, T.TimestampType() if c in rr.TIMESTAMP_COLUMNS else T.BooleanType() if c == 'needs_recheck'
                  else T.IntegerType() if c == 'priority_rank' else T.StringType())
    for c in rr.CASE_COLUMNS])
DECISION_SCHEMA = T.StructType([
    T.StructField(c, T.TimestampType() if c in ('arrived_at', 'routed_at') else T.BooleanType() if c == 'created'
                  else T.StringType()) for c in rr.DECISION_COLUMNS])

def records(df: pd.DataFrame) -> list[tuple]:
    out = []
    for row in df.astype(object).where(df.notna(), None).itertuples(index=False):
        out.append(tuple(v.to_pydatetime() if isinstance(v, pd.Timestamp) else int(v) if type(v).__name__ == 'int64'
                         else v for v in row))
    return out

cases = spark.createDataFrame(records(result['cases']), CASE_SCHEMA)  # noqa: F821
if not spark.catalog.tableExists('review_cases'):  # noqa: F821
    cases.limit(0).write.format('delta').saveAsTable('review_cases')
have = {f.name for f in spark.table('review_cases').schema}  # noqa: F821
missing = [f for f in CASE_SCHEMA if f.name not in have]
if missing:  # add columns only. Existing columns and rows stay
    spark.sql('ALTER TABLE review_cases ADD COLUMNS (' + ', '.join(f'{f.name} {f.dataType.simpleString()}' for f in missing) + ')')  # noqa: F821
cases.createOrReplaceTempView('routed_cases')
cols = [f.name for f in CASE_SCHEMA]
spark.sql(f"""MERGE INTO review_cases t USING routed_cases s ON t.case_key = s.case_key
WHEN MATCHED THEN UPDATE SET {', '.join(f't.{c} = s.{c}' for c in cols)}
WHEN NOT MATCHED THEN INSERT ({', '.join(cols)}) VALUES ({', '.join(f's.{c}' for c in cols)})""")  # noqa: F821

decisions = spark.createDataFrame(records(result['decisions']), DECISION_SCHEMA)  # noqa: F821
decisions.write.mode('overwrite').option('overwriteSchema', 'true').format('delta').saveAsTable('review_decisions')

run_row = [(ROUTED_AT, RUN_ID, len(service), len(result['cases']), len(result['rejected']), len(result['exceptions']),
            json.dumps(SUMMARY))]
run_schema = 'routed_at timestamp, score_run_id string, service_events int, cases int, rejected_events int, exceptions int, summary string'
spark.createDataFrame(run_row, run_schema).write.mode('append').format('delta').saveAsTable('review_routing_runs')  # noqa: F821
{t: spark.table(t).count() for t in ('review_cases', 'review_decisions', 'review_routing_runs')}  # noqa: F821

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Checks

# CELL ********************

assert not result['rejected'], f"Events that do not match the contract: {result['rejected'][:3]}"
counts = spark.table('review_cases').groupBy('case_key').count().where('count > 1').count()  # noqa: F821
assert counts == 0, 'review_cases holds a duplicate case_key'
notebookutils.notebook.exit(json.dumps(SUMMARY))  # noqa: F821

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
