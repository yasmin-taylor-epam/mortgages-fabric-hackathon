# Activator rules

**Status:** Proposed design. Nobody has built these rules in Fabric yet. The Fabric workspace is not ready.

Two independent rules send candidates to one Power Automate flow (`../power_automate/review-case-writer.md`). The flow makes every routing decision. The rules only select candidates. This keeps one tested routing order (`../homebridge_action/router.py`).

Source: blueprint section 03, routing view. Microsoft documents [Activator custom actions that trigger Power Automate flows](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-trigger-power-automate-flows). That capability does not confirm tenant access.

## Rule 1: service rule

| Setting | Value |
| --- | --- |
| Source | Service events from the KQL function `ServiceEvents()` in `kql/04_service_and_360.kql` (Data and replay): `portal_fault_verified`, `payment_support_requested`, `payment_support_closed`, `payment_reversed`. |
| Object | Mortgage cycle. Key: `mortgage_id` + `cycle_id`. |
| Condition | Every event. No score and no threshold (INV-1). |
| Action | Custom action: start the review-case writer flow. |

## Rule 2: risk rule

| Setting | Value |
| --- | --- |
| Source | `risk_updated` events. Only completed runs publish them (INV-5). |
| Object | Mortgage cycle. Key: `mortgage_id` + `cycle_id`. |
| Condition | Every event with `score` at or above the frozen threshold. Candidate: 0.5593, the ten-review capacity threshold from release `hb-refi-clf:1` on synthetic validation data (`../policy/routing-policy.candidate.json`). It is illustrative. The team has not confirmed it. |
| Action | Custom action: start the review-case writer flow. |

The flow checks the score age and payment-support status again. A rule never authorises a case by itself.

## Custom-action field map

Activator passes these fields to the flow. Names match `../contracts/consumer-contract.md`.

| Flow input | Service rule | Risk rule |
| --- | --- | --- |
| `event_id` | yes | yes |
| `event_type` | the service event type | `risk_updated` |
| `schema_version` | — | yes |
| `mortgage_id`, `cycle_id` | yes | yes |
| `occurred_at`, `ingested_at` | yes | `ingested_at` |
| `run_id`, `snapshot_id`, `model_version`, `cluster_model_version`, `segment_id` | — | yes |
| `scored_at`, `cutoff_at`, `target`, `score` | — | yes |
| `evidence_ids` | yes | yes |

## Rules

- No rule sends a customer message (INV-6).
- Both rules stay active when scoring fails. Service alerts do not depend on scoring (T05).
- Activator may deliver an event more than once. The unique `case_key` in SharePoint prevents a duplicate case (T01). It does not promise exactly-once delivery.
- Store no connection secret in an exported rule definition.
