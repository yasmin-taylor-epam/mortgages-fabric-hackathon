# Review-case writer flow (Power Automate)

**Status:** Proposed design. Nobody has built this flow yet. Power Automate licences and connector permissions are not confirmed.

The flow follows the tested local reference step by step. If the two differ, the tested reference (`../homebridge_action/`) is correct and this design changes.

| Flow step | Local reference |
| --- | --- |
| Steps 1 to 3 | `CaseWriter.handle` |
| Step 4 | `router.route` |
| Step 5 | `CaseWriter._write_with_retry` |
| Step 6 | `CaseWriter._hold` and `CaseWriter._recheck` |

## Trigger

"When an Activator rule is triggered". The inputs are the custom-action fields in `../activator/rules.md`.

## Steps

1. **Record the alert time.** Set `alert_received_at` to `utcNow()`.
2. **Read the current context.** Read the account context row for `mortgage_id` + `cycle_id`: `identity_status`, `payment_support_status`, `owner_id` and `context_version`. For a risk candidate, also read `score_runs.status` for `run_id`. Read these values on every run and every retry. A frozen snapshot cannot authorise a case.
3. **Audit the decision.** After step 4, add one item to *HomeBridge Review Case Audit* with `action = decision`.
4. **Route.** Apply these checks in this order. The first match wins.

   | Check | Result |
   | --- | --- |
   | Context missing or `identity_status = unknown` | Quarantine: `unknown_identity`. |
   | `identity_status = conflict` | Quarantine: `conflicting_identity`. |
   | Service event `payment_support_requested` | Support case for the payment-support team. Hold any open renewal case. |
   | Service event `portal_fault_verified` | Technical case for renewal-portal technical support. |
   | Service event `payment_reversed` | No new case. Flag open support and renewal cases for a recheck. Request rescoring. |
   | Service event `payment_support_closed` | No case. A person releases any hold. |
   | Risk candidate and `payment_support_status = active` | Support case. Hold any open renewal case. No score check (INV-1). |
   | Risk candidate and run status is not `completed` | No case: `run_not_completed` (INV-5). |
   | Score older than the policy limit | No case: `stale_score`. |
   | `payment_support_status = unknown` | Hold the renewal review. Add an exception. |
   | Score below the frozen threshold | No case: `below_threshold`. |
   | No `owner_id` | Hold the renewal review. Add an exception. |
   | Otherwise | Renewal case for the servicing adviser in `owner_id`. |

   Quarantine and held reviews add an item to *HomeBridge Routing Exceptions*.
5. **Write the case.** Set `case_key` to `mortgage_id|cycle_id|action_family`. Use "Create item" on *HomeBridge Review Cases*.
   - Duplicate key: SharePoint rejects the item because `case_key` must be unique. Use "Get items" with a filter on `case_key`. Use the existing case. Add an audit item `duplicate_key_read_existing` (T01, INV-3).
   - Other failure: add an exception item `case_write_failed` with the attempt number. Retry with the same `case_key`. Go back to step 2 first, so the payment-support check runs again (T06).
   - After the last attempt: add an exception item `case_write_retries_exhausted` and an audit item `case_write_failed`. The run ends as failed, so an operator sees it.
6. **Apply holds and rechecks.** Change an open renewal case to `on_hold`. Do not close it or delete it. Add an audit item for each change. Earlier audit items never change.

## Settings to confirm before the build

- The retry count and the interval. The local reference uses three attempts.
- The timeout and the owner of exceptions.
- The connection identity. It needs add and edit rights on the case list. It needs add rights only on the audit list.

## Rules

- The flow sends no email, Teams message or other customer message. It makes no rate or credit decision (INV-6).
- Payment-support priority does not depend on refinancing risk or loan balance.
- Do not put credentials or connection secrets in an exported flow package. Use connection references.
- The `store` column is `sharepoint` for cases that this flow writes.
