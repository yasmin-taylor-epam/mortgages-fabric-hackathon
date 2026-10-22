# Data

Synthetic data, data contracts and replay fixtures for the fictional mortgage provider HomeBridge.

**Owner:** Data and replay work package.

## Folders

| Folder | Contents |
| --- | --- |
| `contracts/` | One schema per dataset in the blueprint data contract (section 02). |
| `generator/` | The Python synthetic data generator. |
| `fixtures/` | Replay events for the demo failure cases T01 to T07. |
| `output/` | Generated files. Git ignores this folder. |

## Datasets

The blueprint (`site/index.html`, section 02) defines these datasets:

- Mortgage account: one loan account and fixed-rate deal cycle.
- Payment: one posted ledger change.
- Product-transfer request: one request state change.
- Source servicing case: one recorded state change.
- Servicing contact: one customer contact linked to a source case.
- Feature snapshot: one mortgage, deal cycle and cutoff time.
- Observed outcome: one mortgage and deal cycle. It stays separate from the feature snapshot.
- Segment membership: one account, cycle and scoring run.

## Rules

- All data is synthetic. Never add real customer records.
- The key is `mortgage_id` + `cycle_id`. The cycle identifies a fixed-rate deal, not a scoring run.
- Every event carries an event time (`occurred_at`) and an ingestion time (`ingested_at`).
- Generate outcome labels from separate hidden factors, not from the alert rule.
- Include complete outcomes, reversals, delayed feeds and successful transfers.
- Include three contacts about one unresolved problem and three contacts about separate enquiries.
- Label any future payment figure as an illustration. It is not an offer or an affordability decision.
