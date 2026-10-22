# Action

Alert routing and review-case creation: Activator rules, Power Automate flows and the SharePoint review-case list.

**Owner:** Dashboard and action work package.

**Status:** The routing and the case writer run and pass tests locally. They use a SQLite file in place of SharePoint. Nobody has built the Activator rules, the Power Automate flow or the SharePoint lists yet. Power Automate licences and connector permissions are not confirmed. The Fabric workspace is not ready.

## Contents

| Path | Contents |
| --- | --- |
| `contracts/consumer-contract.md` | The data that `action/` and `reports/` read from the other work packages. |
| `homebridge_action/` | Local reference: routing (`router.py`), the idempotent case writer (`case_writer.py`), the SQLite case store (`store.py`) and a replay command (`cli.py`). |
| `activator/rules.md` | The proposed service rule and risk rule, and the custom-action field map. |
| `power_automate/review-case-writer.md` | The proposed flow. It follows the local reference step by step. |
| `sharepoint/review-cases.list.json` | The three proposed lists: review cases, audit and routing exceptions. |
| `sharepoint/provision-list.ps1` | A PnP.PowerShell script that creates the lists. It takes the site URL as a parameter. It holds no credentials. Nobody has run it yet. |
| `tests/` | pytest tests. `tests/fixtures/` holds hand-written synthetic events, context and an illustrative policy. |

## Routing order

1. An active payment-support request goes to trained servicing staff first, whatever the churn score (INV-1).
2. A verified renewal-portal software fault goes to technical support.
3. An eligible external-refinancing risk candidate goes to a servicing adviser for a renewal review.

`router.py` applies these checks in order. The first match wins.

| Check | Result |
| --- | --- |
| No account context, or `identity_status` is not `ok` | Quarantine (T02). |
| Payment-support request, or a risk candidate while payment support is active | `support` case. Any open renewal case goes on hold. |
| Verified portal fault | `technical` case. |
| Payment reversed | No new case. Open cases are flagged for a recheck. Rescoring is requested (T04). |
| Payment-support request closed | No case. A person releases any hold. |
| Risk candidate from a run that is not `completed` | No case (INV-5, T05). |
| Score older than the policy limit | No case. |
| Payment-support status unknown | Renewal review held. |
| Score below the threshold | No case. |
| No review owner | Renewal review held. |
| Otherwise | `renewal` case for the servicing adviser. |

## Case writer

- The case key is `mortgage_id|cycle_id|action_family`. The store rejects a second row for a key (INV-3).
- A duplicate key reads the existing case (T01). A replay into the same store adds no case.
- A transient write failure retries with the same key. Each failure goes to the exception register. After the last attempt, the error stays visible and no case exists (T06).
- The writer reads the account context and run status on every delivery and every retry.
- The audit log is append-only. The store rejects updates and deletes. Every decision is audited, including decisions that create no case.
- Every case records evidence IDs, routing policy, assigned owner, decision reason and separate ingestion, scoring, alert and creation times. Score-based cases also record model version, cluster model version, run and snapshot.
- Every local case has `store = local`. A local run never claims that SharePoint wrote a case.

## Run the tests and the demo replay

```powershell
python -m venv .venv
.venv\Scripts\pip install -r action\requirements.txt
.venv\Scripts\python -m pytest action
```

The demo replay uses illustrative fictional accounts. It shows payment-support-first routing, a replayed event, a failed scoring run, quarantine and a reversal:

```powershell
cd action
..\.venv\Scripts\python -m homebridge_action.cli --events tests\fixtures\replay.jsonl --context tests\fixtures\context.json --policy tests\fixtures\policy.json --now 2026-11-03T10:00:00Z
```

Add `--db cases.sqlite` to keep the store between runs. Do not commit that file.

## Rules

- INV-3: at most one open review case exists per `mortgage_id` + `cycle_id` + `action_family`. A duplicate write reads the existing case (T01, T06).
- INV-6: the prototype sends no customer messages. It makes no rate or credit decisions. A test checks that the package imports no mail or network module.
- Payment-support priority does not depend on refinancing risk or loan balance. The account context holds no balance.
- The risk threshold in `tests/fixtures/policy.json` is illustrative. The Models and evaluation work package freezes the real threshold on validation data.
- Never put credentials or connection secrets in an exported flow, rule or script.
