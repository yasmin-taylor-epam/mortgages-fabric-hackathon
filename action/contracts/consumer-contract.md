# Consumer contract: Dashboard and action

**Status:** Draft. Sent to the Data and replay and the Models and evaluation sessions for agreement.

This file lists the data that `action/` and `reports/` read. The producing work packages own the schemas. If a producer schema differs from this file, the producer schema wins and this file changes.

The source of every field is the blueprint (`site/index.html`, sections 02, 03 and 05). Items marked **Proposed** are not in the blueprint. They need agreement.

## 1. `risk_updated` event

**Producer:** Models and evaluation. **Consumer:** the Activator risk rule and the review-case writer.

| Field | Type | Notes |
| --- | --- | --- |
| `event_id` | string | Stable. A publication retry reuses the same value. |
| `mortgage_id` | string | |
| `cycle_id` | string | Identifies the fixed-rate deal, not the scoring run. |
| `run_id` | string | The scoring run. Its status is in `score_runs`. |
| `snapshot_id` | string | The frozen feature snapshot. |
| `model_version` | string | Registered classifier version. |
| `cluster_model_version` | string | Registered cluster model version. |
| `segment_id` | string | |
| `scored_at` | ISO 8601, UTC | |
| `cutoff_at` | ISO 8601, UTC | |
| `target` | string | For example `external_refinancing_90d`. |
| `score` | number, 0 to 1 | Refinancing risk. |
| `evidence_ids` | array of string | References to evidence rows. No borrower notes. |
| `ingested_at` | ISO 8601, UTC | **Proposed.** Time the event reached Eventhouse. The case writer records it. |

Rules that the consumer relies on:

- INV-5: the event fires only after the run succeeds. The case writer still checks `score_runs.status = completed`. It drops any other event and records the reason.
- The consumer treats a score older than the policy limit as stale. It creates no renewal case from a stale score.

## 2. Service event

**Producer:** Data and replay (Eventstream replay). **Consumer:** the Activator service rule and the review-case writer.

**Proposed** shape. The blueprint names the events but not the fields.

| Field | Type | Notes |
| --- | --- | --- |
| `event_id` | string | Original source event ID. |
| `event_type` | string | One of `payment_support_requested`, `payment_support_closed`, `portal_fault_verified`, `payment_reversed`. |
| `mortgage_id` | string | |
| `cycle_id` | string | |
| `occurred_at` | ISO 8601, UTC | Event time. |
| `ingested_at` | ISO 8601, UTC | Ingestion time. |
| `evidence_ids` | array of string | Source references. No sensitive notes. |

`portal_fault_verified` means a verified renewal-portal software fault. A request rejection or an abandoned request is not a portal fault.

## 3. Current account context (lookup at routing time)

**Producer:** Data and replay (the account-context copy, blueprint step 7). **Consumer:** the review-case writer. The writer reads this on every delivery and every retry.

| Field | Type | Notes |
| --- | --- | --- |
| `mortgage_id`, `cycle_id` | string | Key. |
| `identity_status` | `ok`, `unknown`, `conflict` | Anything other than `ok` goes to quarantine (T02). |
| `payment_support_status` | `active`, `none`, `unknown` | `unknown` holds a renewal review. |
| `owner_id` | string | The servicing adviser who owns renewal reviews. |
| `context_version` | string | The version of the copy. |

The context holds no loan balance. Payment-support priority must not depend on balance or risk.

## 4. Scoring run status: `score_runs`

**Producer:** Models and evaluation.

| Field | Type | Notes |
| --- | --- | --- |
| `run_id` | string | Key. |
| `run_kind` | `scheduled`, `checkpoint`, `manual` | |
| `status` | `running`, `completed`, `failed` | Only `completed` outputs are visible. |
| `started_at`, `completed_at` | ISO 8601, UTC | `completed_at` is null unless the run completed. |

## 5. Gold tables for the report (Direct Lake)

Direct Lake does not allow calculated columns on Lakehouse tables. Each table therefore needs its join keys as stored columns.

| Table | Grain | Producer | Fields the report reads |
| --- | --- | --- | --- |
| `mortgage` | One row per `mortgage_id`. **Proposed.** | Data and replay | `mortgage_id`, `product_type` |
| `mortgage_account` | One mortgage and deal cycle. | Data and replay | `account_key`, `mortgage_id`, `cycle_id`, `deal_end_at`, `balance`, `current_payment`, `product_type`, `owner_id` |
| `payments` | One posted ledger change. | Data and replay | `entry_id`, `mortgage_id`, `due_at`, `posted_at`, `ingested_at`, `amount_due`, `amount_received`, `reversal_of` |
| `feed_status` | One row per source feed. **Proposed.** | Data and replay | `feed_name`, `last_ingested_at`, `expected_interval_minutes` |
| `transfer_request_events` | One request state change. | Data and replay | `event_id`, `application_id`, `mortgage_id`, `occurred_at`, `ingested_at`, `stage`, `error_code`, `status` |
| `servicing_case` | One row per source `case_id`. **Proposed.** | Data and replay | `case_id`, `mortgage_id`, `category`, `support_route` |
| `servicing_case_states` | One recorded case state change. | Data and replay | `case_id`, `mortgage_id`, `changed_at`, `recorded_at`, `category`, `status`, `support_route` |
| `servicing_contacts` | One customer contact. | Data and replay | `contact_id`, `case_id`, `mortgage_id`, `occurred_at`, `ingested_at` |
| `score_runs` | One scoring run. | Models and evaluation | See section 4. |
| `account_scores` | One account, cycle and run. | Models and evaluation | `account_key`, `mortgage_id`, `cycle_id`, `run_id`, `snapshot_id`, `model_version`, `score`, `scored_at`, `cutoff_at` |
| `feature_attributions` | One account, run and feature. | Models and evaluation | `account_key`, `run_id`, `feature_name`, `contribution` |
| `segment_membership` | One account, cycle and run. Append only. | Models and evaluation | `account_key`, `mortgage_id`, `cycle_id`, `run_id`, `segment_id`, `cluster_model_version`, `assigned_at`, `valid_from`, `valid_to` |
| `review_cases` | One review case. | Dashboard and action | The SharePoint list columns in `action/sharepoint/review-cases.list.json`. |
| `report_calendar` | One row per date. **Proposed.** | Dashboard and action | `date` |

Conventions that the report relies on:

- `account_key` is `mortgage_id + "|" + cycle_id`, stored as a column. **Proposed.**
- A reversal row sets `reversal_of` to the original `entry_id`. It negates the original `amount_received`. **Proposed.** The report then sums rows to get the position.
- A case `status` of `resolved` or `closed` means the problem is resolved. Any other status means unresolved. **Proposed.**
- `segment_membership.valid_to` is null for the current row. Earlier rows never change.
- Times are UTC.

## 6. What this package produces

- Review cases with the key `mortgage_id + cycle_id + action_family`. `action_family` is `support`, `technical` or `renewal`.
- An append-only audit log and an exception register. See `action/README.md`.
- A rescoring request when a payment is reversed (T04). The scheduled scoring pipeline owns the rescoring itself.
