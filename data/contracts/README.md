# Data contracts

One JSON Schema (draft 2020-12) file per dataset. HomeBridge is a fictional lender. All data is synthetic.

The schema files are the source of truth. If another document differs, the schema wins. To change a contract, change the schema and the generator in the same pull request, then tell the sessions that read it.

## Conventions

- Key: `mortgage_id + cycle_id`. The cycle identifies a fixed-rate deal, not a scoring run.
- Every event carries an event time and an ingestion time. `x-event-time` and `x-ingestion-time` in each schema name the two columns.
- INV-2: a model input is usable at a cutoff only when both times are at or before `cutoff_at`.
- Times are UTC. In Parquet they are `timestamp[us, UTC]`. In JSON they are ISO 8601 strings.
- `x-primary-key` lists the key columns. `x-grain` states the grain.
- Nested objects (`payload`, `source_watermark`) are JSON text in Parquet.
- `hbdata.contracts.arrow_schema(name)` derives the Parquet schema from a contract.

## Datasets

| Contract | Grain | Key | Event time / ingestion time | Producer | Main readers |
| --- | --- | --- | --- | --- | --- |
| `mortgage_account` | Loan account and fixed-rate deal cycle | `mortgage_id`, `cycle_id` (`account_key` stored) | `recorded_at` / `ingested_at` | Data and replay | Models, Dashboard |
| `account_party` | Borrower mapping | `mortgage_id`, `party_id`, `valid_from` | `valid_from` / `ingested_at` | Data and replay | Validation |
| `payment` | Posted ledger change | `entry_id` | `posted_at` / `ingested_at` | Data and replay | Models, Dashboard |
| `transfer_request_event` | Product-transfer request state change | `event_id` | `occurred_at` / `ingested_at` | Data and replay | Models, Dashboard, KQL |
| `servicing_case_event` | Source case state change | `case_event_id` | `changed_at` / `recorded_at` | Data and replay | Models, Dashboard |
| `servicing_contact` | Customer contact linked to a case | `contact_id` | `occurred_at` / `ingested_at` | Data and replay | Models, Dashboard |
| `observed_outcome` | Refinancing outcome per deal cycle | `mortgage_id`, `cycle_id` | `outcome_at` / `outcome_observed_at` | Data and replay | Models (training and evaluation only) |
| `feature_snapshot` | Frozen inputs per cycle and cutoff | `snapshot_id` | `cutoff_at` | Data and replay | Models |
| `mortgage` | Report dimension per account | `mortgage_id` | – | Data and replay | Dashboard |
| `servicing_case` | Report dimension per source case | `case_id` | – | Data and replay | Dashboard |
| `feed_watermark` | Feed freshness per check | `dataset`, `checked_at` | – | Data and replay | Dashboard, KQL |
| `quarantine_record` | Rejected source record | `quarantine_id` | – | Data and replay | Dashboard (exceptions) |
| `live_event` | Eventstream envelope | `event_id` | `occurred_at` / Eventhouse ingestion | Data and replay (replay) | KQL |
| `service_event` | Service event from `ServiceEvents()` | `event_id` | `occurred_at` / `ingested_at` | Data and replay (KQL) | Dashboard (Activator, case writer) |
| `account_context` | Context copy in Eventhouse | `mortgage_id`, `cycle_id`, `context_version` | – | Data and replay (`pl_copy_context`) | Dashboard (case writer), KQL |
| `segment_membership` | Segment per account, cycle and run. Append only. | `mortgage_id`, `cycle_id`, `run_id` | – | Models | Dashboard |
| `risk_updated` | Event after a completed scoring run | `event_id` | `scored_at` | Models | Dashboard |

`segment_membership` and `risk_updated` are owned by the Models and evaluation package. They are here because the blueprint data contract and event contract list them.

## Labels and outcomes (INV-4)

- `label = 1`: confirmed external refinancing between the checkpoint and deal end plus 30 days.
- `label = 0`: product transfer or continued servicing, assigned only after the window ends.
- No label: sale, bereavement, redemption with no known reason, or an incomplete window (`label_status` is `excluded` or `pending`). An unlabelled outcome never counts as retained.
- `outcome_observed_at` is when HomeBridge confirmed the outcome. A simulated model release may use only labels observed before it.

## Feature snapshot columns

Prefixes group the features:

- `pt_`: product-transfer request signals from the journey queries.
- `svc_`: servicing history. Payment-difficulty cases are excluded because they are sensitive.
- `ctx_`: payment and routing context. These are not recommended refinancing inputs. `ctx_payment_support_open` serves INV-1 routing. A stale payment feed sets the arrears fields to null (T03).

`feature_snapshot.schema.json` describes every column.
