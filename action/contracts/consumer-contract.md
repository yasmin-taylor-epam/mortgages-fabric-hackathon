# Consumer contract: Dashboard and action

**Status:** Agreed by both producers on 22 October 2026. Data and replay agreed sections 2, 3 and their rows of section 5. Their schemas are in `data/contracts/*.schema.json` (PR #8). Models and evaluation agreed sections 1, 4 and their rows of section 5. They write the Gold tables to `data/output/lakehouse/gold/`. The tests in `action/tests/test_action_data_integration.py` and `reports/tests/test_report_data_integration.py` check this file against the producer schemas, fixtures and generated tables.

This file lists the data that `action/` and `reports/` read. The producing work packages own the schemas. If a producer schema differs from this file, the producer schema wins and this file changes.

The source of every field is the blueprint (`site/index.html`, sections 02, 03 and 05). Items marked **Proposed** are not in the blueprint. They need agreement.

IDs follow the Data and replay patterns: `mortgage_id` is `HB-M-` and six digits, and `cycle_id` is `C` and two digits.

## 1. `risk_updated` event

**Producer:** Models and evaluation. **Consumer:** the Activator risk rule and the review-case writer. `data/contracts/risk_updated.schema.json` holds the current schema.

| Field | Type | Notes |
| --- | --- | --- |
| `event_id` | string | Stable. A publication retry reuses the same value. |
| `event_type` | `risk_updated` | The case writer uses `event_type` to tell a `risk_updated` event from a service event. |
| `schema_version` | `1.0` | |
| `mortgage_id` | string | |
| `cycle_id` | string | Identifies the fixed-rate deal, not the scoring run. |
| `run_id` | string | The scoring run. Its status is in `score_runs`. |
| `snapshot_id` | string | The frozen feature snapshot. |
| `model_version` | string | Registered classifier version. |
| `cluster_model_version` | string | Registered cluster model version. |
| `segment_id` | string | |
| `scored_at` | ISO 8601, UTC | |
| `cutoff_at` | ISO 8601, UTC | |
| `target` | string | `confirmed_external_refinancing` (fixed by the Data and replay contract). |
| `score` | number, 0 to 1 | Refinancing risk. |
| `evidence_ids` | array of string | References to evidence rows. No borrower notes. |

The payload has no `ingested_at`. The Activator source passes the Eventhouse ingestion time to the flow as `ingested_at`.

Rules that the consumer relies on:

- INV-5: the event fires only after the run succeeds. The case writer still checks `score_runs.status = completed`. It drops any other event and records the reason.
- The consumer treats a score older than the policy limit as stale. It creates no renewal case from a stale score.

## 2. Service event

**Producer:** Data and replay. The KQL function `ServiceEvents()` and `data/contracts/service_event.schema.json` output this shape. **Consumer:** the Activator service rule and the review-case writer.

**Agreed** with Data and replay on 22 October 2026.

| Field | Type | Notes |
| --- | --- | --- |
| `event_id` | string | Original source event ID. |
| `event_type` | string | One of `payment_support_requested`, `payment_support_closed`, `portal_fault_verified`, `payment_reversed`. |
| `mortgage_id` | string | |
| `cycle_id` | string | |
| `occurred_at` | ISO 8601, UTC | Event time. |
| `ingested_at` | ISO 8601, UTC | Eventhouse ingestion time. |
| `evidence_ids` | array of string | Source references. No sensitive notes. |

`portal_fault_verified` fires only for `error_class = software_fault` and `fault_verified = true`. A request rejection or an abandoned request never produces it.

## 3. Current account context (lookup at routing time)

**Producer:** Data and replay (`data/contracts/account_context.schema.json`, the account-context copy in blueprint step 7). **Consumer:** the review-case writer. The writer reads this on every delivery and every retry.

| Field | Type | Notes |
| --- | --- | --- |
| `mortgage_id`, `cycle_id` | string | Key. |
| `identity_status` | `ok`, `conflict` | `conflict` goes to quarantine (T02). |
| `payment_support_status` | `active`, `none`, `unknown` | `unknown` holds a renewal review. It is `unknown` when the servicing feed is stale. |
| `owner_id` | string | The servicing adviser who owns renewal reviews. |
| `context_version` | string | The version of the copy. |

An unknown account has no row. The writer treats a missing row as unknown and quarantines the candidate (T02). The copy also carries `deal_start_at`, `deal_end_at`, `checkpoint_at` and `copied_at`. The writer does not need them.

The context holds no loan balance. Payment-support priority must not depend on balance or risk.

## 4. Scoring run status: `score_runs`

**Producer:** Models and evaluation.

| Field | Type | Notes |
| --- | --- | --- |
| `run_id` | string | Key. |
| `run_kind` | `scheduled`, `checkpoint`, `manual` | |
| `status` | `running`, `completed`, `failed` | Only `completed` outputs are visible. |
| `started_at`, `completed_at` | ISO 8601, UTC | `completed_at` is null unless the run completed. |

The table also holds `model_version`, `cluster_model_version`, `cutoff_at` and `error`. `error` is null unless the run failed. This package does not read them. A failed run writes no outbox rows and publishes no `risk_updated` event. Agreed.

## 5. Gold tables for the report (Direct Lake)

Direct Lake does not allow calculated columns on Lakehouse tables. Each table therefore needs its join keys as stored columns.

| Table | Grain | Producer | Fields the report reads |
| --- | --- | --- | --- |
| `mortgage` | One row per `mortgage_id`. | Data and replay | `mortgage_id`, `product_type`, `current_cycle_id` |
| `mortgage_account` | One mortgage and deal cycle. | Data and replay | `account_key`, `mortgage_id`, `cycle_id`, `deal_end_at`, `balance`, `current_payment`, `product_type`, `owner_id` |
| `payment` | One posted ledger change. | Data and replay | `entry_id`, `entry_type`, `mortgage_id`, `due_at`, `posted_at`, `ingested_at`, `amount_due`, `amount_received`, `reversal_of` |
| `feed_watermark` | One row per dataset. | Data and replay | `dataset`, `checked_at`, `max_ingested_at`, `max_event_at`, `row_count`, `stale_threshold_hours`, `is_stale` |
| `transfer_request_event` | One request state change. | Data and replay | `event_id`, `application_id`, `mortgage_id`, `occurred_at`, `ingested_at`, `stage`, `status`, `error_code`, `error_class`, `fault_verified` |
| `servicing_case` | One row per source `case_id`. | Data and replay | `case_id`, `mortgage_id`, `category`, `support_route`, `opened_at`, `latest_status`, `resolved_at` |
| `servicing_case_event` | One recorded case state change. | Data and replay | `case_id`, `mortgage_id`, `changed_at`, `recorded_at`, `category`, `status`, `support_route` |
| `servicing_contact` | One customer contact. | Data and replay | `contact_id`, `case_id`, `mortgage_id`, `occurred_at`, `ingested_at` |
| `score_runs` | One scoring run. | Models and evaluation | See section 4. |
| `account_scores` | One account, cycle and run. | Models and evaluation | `account_key`, `mortgage_id`, `cycle_id`, `run_id`, `snapshot_id`, `model_version`, `score`, `scored_at`, `cutoff_at` |
| `feature_attributions` | One account, run and feature. | Models and evaluation | `account_key`, `run_id`, `feature_name`, `feature_value`, `contribution`, `rank` (1 = largest absolute contribution) |
| `segment_membership` | One account, cycle and run. Append only. | Models and evaluation | `account_key`, `mortgage_id`, `cycle_id`, `run_id`, `segment_id`, `cluster_model_version`, `assigned_at`, `valid_from`, `valid_to` |
| `review_cases` | One review case. | Dashboard and action | The SharePoint list columns in `action/sharepoint/review-cases.list.json`. |
| `report_calendar` | One row per date. **Proposed.** | Dashboard and action | `date` |

Conventions that the report relies on:

- `account_key` is `mortgage_id + "|" + cycle_id`, stored as a column on `mortgage_account`, `account_scores`, `feature_attributions` and `segment_membership`. Agreed.
- A reversal row has `entry_type = reversal`, `reversal_of` = the original `entry_id`, `amount_received` = minus the original amount and `amount_due = 0`. The other entry types are `scheduled_collection` and `manual_payment`. Manual payments also have `amount_due = 0`. Summing rows gives the position. Agreed.
- Case status is `opened`, `in_progress`, `resolved` or `reopened`. A case is resolved only when its latest state at the report time is `resolved`. Both `changed_at` and `recorded_at` must be at or before that time. `reopened` means unresolved. Agreed.
- `feed_watermark.is_stale` comes from the validation step. `stale_threshold_hours` is a number. The report also treats the feed as stale when `checked_at` or `max_ingested_at` is older than `stale_threshold_hours`. The `payment` dataset row drives the arrears display. Agreed.
- `segment_membership` rows are never changed. `valid_to` is always null when a row is written, and no later run sets it. The report takes the latest row that started at or before the chosen date. It does not read `valid_to`. Agreed.
- Times are UTC.

## 6. What this package produces

- Review cases with the key `mortgage_id + cycle_id + action_family`. `action_family` is `support`, `technical` or `renewal`.
- An append-only audit log and an exception register. See `action/README.md`.
- A rescoring request when a payment is reversed (T04). The scheduled scoring pipeline owns the rescoring itself.

### T04: what "review starts" means here

A `payment_reversed` event creates no new review case. A reversal is not proof of default. It is not an active payment-support request, so it cannot open a support case. It is not an eligible risk event, so it cannot open a renewal review. The writer does three things:

1. It flags every open support or renewal case for the same mortgage and cycle as `needs_recheck`. The assigned person reviews it.
2. It records a `rescore_requested` audit entry with the reversal evidence IDs.
3. It leaves earlier audit entries unchanged.

A new renewal review starts only if the rescoring run publishes an eligible `risk_updated` event.
