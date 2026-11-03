# Data

Synthetic data, data contracts and replay fixtures for the fictional mortgage provider HomeBridge.

**Owner:** Data and replay work package.

**Status:** Runs locally. Nothing here has run in Fabric yet. The Fabric workspace is not ready.

## Folders

| Folder | Contents |
| --- | --- |
| `contracts/` | One JSON Schema per dataset in the blueprint data contract (section 02), plus the event contracts. See [contracts/README.md](contracts/README.md). |
| `hbdata/` | Python package: generator, validation, feature snapshots, journey queries (pandas twin of the KQL), replay and local pipeline stand-ins. |
| `fixtures/` | Replay events and expected results for failure cases T01 to T07 and the demo. See [fixtures/README.md](fixtures/README.md). |
| `tests/` | pytest suite for the invariants and failure cases. |
| `output/` | Generated files. Git ignores this folder. |

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r data\requirements.txt
cd data
..\.venv\Scripts\python -m hbdata all     # generate, load raw, validate, snapshot, copy context, backfill Eventhouse
..\.venv\Scripts\python -m pytest -q      # about 30 seconds
```

`python -m hbdata --help` lists every command.

## What `all` writes

| Path under `data/output/` | Blueprint step | Contents |
| --- | --- | --- |
| `exports/` | Source | One Parquet file per source dataset and `manifest.json` (seed, counts, label rates, personas, injected anomalies). |
| `lakehouse/raw/` | 1 (`pl_load_raw`) | Append-only loads. |
| `lakehouse/clean/` | 2 (`01_validate_histories`) | Clean histories, `mortgage`, `servicing_case`, `feed_watermark` and `_validation_report.json`. |
| `lakehouse/quarantine/` | 2 | `quarantine_record.parquet`. |
| `lakehouse/features/` | 2 (`02_feature_snapshots`) | `feature_snapshot.parquet` and a separate `observed_outcome.parquet`. |
| `lakehouse/staging/` | 8 (`pl_export_journeys`) | Journey results at a cutoff. |
| `eventhouse/` | 5 and 7 | Local stand-in for Eventhouse: `raw_events`, `quarantined_events`, `account_context`, `score_context`, `source_watermarks`. |

The Models and evaluation package writes `lakehouse/gold/account_scores.parquet` and `lakehouse/gold/score_runs.parquet`. `pl_copy_context` reads them.

## Generated dataset

Default settings (seed `20261026`, simulated "now" `2026-10-15T00:00:00Z`):

- 3,000 mortgages in six labelled deal-end cohorts (January to June 2026), 500 per cohort.
- 300 mortgages in an open cohort. Their deals end between 25 days before and 89 days after `as_of`. Their outcomes are pending (INV-4).
- 4 scripted personas. All are illustrative.
- About 66,000 payments, 21,000 request events, 20,000 case state changes and 10,000 contacts.
- Positive label rate (confirmed external refinancing): about 20% per labelled cohort. This is a synthetic setting, not a market figure.

Outcome labels come from hidden factors, not from an alert rule. The factors are rate shopping, broker contact, engagement, financial stress, the payment shock from the follow-on rate, and the portal friction the customer experienced. The generator never writes the hidden factors to disk.

The data includes these cases:

- complete and excluded outcomes;
- reversals and failed collections;
- delayed rows, where the event happened before a cutoff but arrived after it;
- five payment-feed outages;
- late case resolutions;
- joint borrowers;
- successful transfers;
- a small, known set of bad records. `manifest.json` lists them, and validation must quarantine them.

### Illustrative personas

| Persona | mortgage_id | Story |
| --- | --- | --- |
| Alex | `HB-M-900001` | Renewal-portal faults stop a product-transfer request. One failed collection, repaid. An unresolved complaint. Open cohort; checkpoint 5 days before `as_of`. |
| Sam | `HB-M-900002` | Completed product transfer. One reversed collection, repaid. Neither proves default. |
| Casey | `HB-M-900003` | Three contacts about one unresolved complaint (T07). |
| Robin | `HB-M-900004` | Three contacts about three separate resolved enquiries (T07). |

## Rules

- All data is synthetic. Never add real customer records.
- The key is `mortgage_id` + `cycle_id`. The cycle identifies a fixed-rate deal, not a scoring run.
- Every event carries an event time and an ingestion time. INV-2 uses both.
- Generate outcome labels from separate hidden factors, not from the alert rule.
- Include complete outcomes, reversals, delayed feeds and successful transfers.
- Include three contacts about one unresolved problem and three contacts about separate enquiries.
- Label any future payment figure as an illustration. It is not an offer or an affordability decision. `follow_on_rate_pct` is an illustrative rate.
- Feature snapshots exclude case notes, names and location. Payment-difficulty cases appear only as the routing flag `ctx_payment_support_open`.
