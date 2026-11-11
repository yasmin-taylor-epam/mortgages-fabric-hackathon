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

# # 06_publish_delta_tables
#
# **Owner:** Integration. **Blueprint step:** 5 (Gold tables for the report). **Status:** Fabric only. First ran in Fabric on 5 November 2026.
#
# The Python packages write Parquet files under `Files/hb/output/lakehouse/`. The Power BI semantic model (Direct Lake) reads Delta tables in the Lakehouse `Tables` area. This notebook copies each Parquet file the model needs into a Delta table with the same name.
#
# - Each run overwrites the Delta table from the full Parquet file. The Parquet files stay the source of truth, and append-only tables stay append-only there.
# - `report_calendar` is rebuilt with one row per date from the earliest deal start to the latest deal end. The Dashboard and action package owns this table. This is a stand-in until its own job exists.
# - `review_cases` is created empty if it does not exist. The real source is the SharePoint review-case list. The copy job is not designed yet.
# - `review_outcomes` is created empty if it does not exist. It holds adviser verdicts. Nothing writes to it yet.
# - `report_settings` is rewritten with one row: the report as-of time from the `AS_OF` parameter (empty means the real time).
#
# This notebook does not run locally. It needs Spark and the attached Lakehouse. All data is synthetic.

# PARAMETERS CELL ********************

OUTPUT_ROOT_REL = 'Files/hb/output'  # relative to the default Lakehouse
AS_OF = None  # report as-of time (ISO 8601, UTC). Empty or None: the report uses the real time.

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# Delta table name -> Parquet path under <output root>/lakehouse. The tests check these names against the semantic model.
TABLE_SOURCES = {
    'mortgage': 'clean/mortgage.parquet',
    'mortgage_account': 'clean/mortgage_account.parquet',
    'payment': 'clean/payment.parquet',
    'servicing_case': 'clean/servicing_case.parquet',
    'servicing_case_event': 'clean/servicing_case_event.parquet',
    'servicing_contact': 'clean/servicing_contact.parquet',
    'transfer_request_event': 'clean/transfer_request_event.parquet',
    'feed_watermark': 'clean/feed_watermark.parquet',
    'account_scores': 'gold/account_scores.parquet',
    'feature_attributions': 'gold/feature_attributions.parquet',
    'segment_membership': 'gold/segment_membership.parquet',
    'score_runs': 'gold/score_runs.parquet',
}
GENERATED_TABLES = ['report_calendar', 'review_cases', 'review_outcomes', 'report_settings']

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

from pathlib import Path

if not Path('/lakehouse/default/Files').exists():
    raise RuntimeError('Attach the HomeBridge Lakehouse as the default Lakehouse. This notebook runs only in Fabric.')

rows = {}
for table, rel in TABLE_SOURCES.items():
    sdf = spark.read.parquet(f'{OUTPUT_ROOT_REL}/lakehouse/{rel}')
    sdf.write.mode('overwrite').option('overwriteSchema', 'true').format('delta').saveAsTable(table)
    rows[table] = spark.table(table).count()
rows

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Report calendar and review cases

# CELL ********************

from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType, TimestampType

acct = spark.table('mortgage_account')
lo, hi = acct.agg(F.min(F.to_date('deal_start_at')), F.max(F.to_date('deal_end_at'))).first()
calendar = spark.sql(f"SELECT explode(sequence(to_date('{lo}'), to_date('{hi}'), interval 1 day)) AS date")
calendar.write.mode('overwrite').option('overwriteSchema', 'true').format('delta').saveAsTable('report_calendar')

if not spark.catalog.tableExists('review_cases'):
    cols = ['case_key', 'account_key', 'mortgage_id', 'cycle_id', 'action_family', 'status', 'assigned_to']
    schema = StructType([StructField(c, StringType()) for c in cols] + [StructField('case_created_at', TimestampType())])
    spark.createDataFrame([], schema).write.format('delta').saveAsTable('review_cases')

if not spark.catalog.tableExists('review_outcomes'):  # adviser verdicts. Nothing writes to it yet
    o_cols = ['case_key', 'decision', 'decided_by']
    o_schema = StructType([StructField(c, StringType()) for c in o_cols] + [StructField('decided_at', TimestampType())])
    spark.createDataFrame([], o_schema).write.format('delta').saveAsTable('review_outcomes')

# One row with the report as-of time. An empty AS_OF writes NULL, and the report then uses the real time.
as_of = F.to_timestamp(F.lit(AS_OF)) if AS_OF else F.lit(None).cast('timestamp')
spark.range(1).select(as_of.alias('as_of'), F.current_timestamp().alias('written_at')) \
    .write.mode('overwrite').option('overwriteSchema', 'true').format('delta').saveAsTable('report_settings')
{t: spark.table(t).count() for t in GENERATED_TABLES}

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# ## Checks
#
# The semantic model needs scored accounts. An empty Gold table means notebook 04 did not finish.

# CELL ********************

for table in ('account_scores', 'segment_membership', 'score_runs'):
    assert rows[table] > 0, f'{table} is empty. Run 04_score_and_segment first.'
print('Delta tables published:', len(TABLE_SOURCES) + len(GENERATED_TABLES))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
