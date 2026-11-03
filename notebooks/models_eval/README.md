# models_eval

**Owner:** Models and evaluation work package. **Status:** Runs locally. Not yet run in Fabric.

This Python package trains, scores and evaluates the refinancing-risk models for the fictional lender HomeBridge. Notebooks `03_train_models`, `04_score_and_segment` and `05_evaluate` call it. The pipeline `pl_scheduled_scoring` runs notebook 04. All data is synthetic.

## Setup

Run these commands from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r notebooks\models_eval\requirements.txt
cd data; ..\.venv\Scripts\python -m hbdata all; cd ..
```

The last command uses the Data and replay generator to write the synthetic inputs to `data/output/`. Git ignores that folder.

## Run

```powershell
cd notebooks
..\.venv\Scripts\python -m models_eval train      # one release: models, thresholds, clusters, MLflow registration
..\.venv\Scripts\python -m models_eval evaluate   # data/output/reports/evaluation_report.md and .json
..\.venv\Scripts\python -m models_eval pipeline --cutoff 2026-10-15T00:00:00Z --kind checkpoint --started-at 2026-10-15T00:05:00Z
```

Set `HB_OUTPUT_ROOT` to use another output folder. MLflow uses a SQLite store at `data/output/mlflow/mlflow.db`. To browse it, run `mlflow ui --backend-store-uri sqlite:///data/output/mlflow/mlflow.db` (this needs the full `mlflow` package). In Fabric, set `HB_MLFLOW_URI=fabric` so that the workspace tracking server is used. Nobody has tested this setting in Fabric yet.

## Tests

```powershell
cd notebooks\models_eval
..\..\.venv\Scripts\python -m pytest            # all tests, including notebook execution
..\..\.venv\Scripts\python -m pytest -m "not slow"
```

The unit tests use a few hand-written synthetic rows. The end-to-end tests build a small lakehouse with the Data and replay generator. This package has no data generator of its own.

| Rule | Test |
| --- | --- |
| INV-2: snapshot data known at the cutoff | `test_contract.py::test_inv2_*`, `test_end_to_end.py::test_scoring_rejects_inv2_breach` |
| INV-4: unknown outcomes are never labelled as retained | `test_contract.py::test_inv4_*` |
| Labels observed before the simulated release | `test_contract.py::test_labels_observed_after_release_are_dropped`, `test_split_drops_validation_labels_observed_after_release` |
| Test-cohort borrowers stay out of training | `test_contract.py::test_split_removes_training_rows_that_share_a_borrower_with_test` |
| INV-5 / T05: a failed run publishes nothing and leaves the last score stale | `test_end_to_end.py::test_scoring_runs_publish_append_and_fail_safely`, `test_t05_fixture.py` |
| Publication retry keeps the same `event_id` | `test_end_to_end.py::test_scoring_runs_publish_append_and_fail_safely` |
| Segment rows are append only | `test_units.py::test_segment_membership_is_append_only`, end-to-end test |
| Lift at ten reviews per run | `test_units.py::test_lift_at_ten_*` |

## Inputs (Data and replay)

The schemas in `data/contracts/` define the inputs. Data and replay owns them.

- `lakehouse/features/feature_snapshot.parquet`: one frozen snapshot for each mortgage, deal cycle and cutoff. The model reads the `pt_` and `svc_` columns, `rate_gap_pct` and `deal_length_months`. It does not read `ctx_` columns, `days_to_deal_end`, the loan balance or the payment amount. See `features.py`.
- `lakehouse/features/observed_outcome.parquet`: rows with `label_status = labelled` only (INV-4). Training and validation use only labels with `outcome_observed_at` before the simulated release.
- `lakehouse/clean/account_party.parquet`: approved borrower links. This file keeps test-cohort borrowers out of training.

## Outputs

Gold tables go in `lakehouse/gold/`. Each table stores `account_key = mortgage_id|cycle_id`. The Dashboard and action consumer contract (`action/contracts/consumer-contract.md` on its branch) lists the columns that the report reads.

| Table | Grain | Write rule |
| --- | --- | --- |
| `score_runs` | One scoring run. `status` is `running`, `completed` or `failed`. | Status changes in place. |
| `account_scores` | One account, cycle and run. Has the score, the segment, the snapshot and the model versions. | Append only. |
| `feature_attributions` | The 10 largest contributions for each account and run, with `rank`, `feature_value` and `contribution_unit`. | Append only. |
| `segment_membership` | One account, cycle and run. `valid_to` stays null. The current segment is the latest row. | Append only. |
| `risk_outbox` | One `risk_updated` event. It has a stable `event_id` and the publication status. | Status changes in place. |

The `risk_updated` payloads match `data/contracts/risk_updated.schema.json`. Locally they go to `data/output/eventstream/risk_updated.jsonl`.

Releases go in `data/output/models/releases/<release_id>/`. Each one has a `release.json` with the frozen settings, thresholds and segment profiles, and the fitted models. MLflow registers the champion classifier as `hb-refi-clf` and the cluster pipeline as `hb-segments`.

See `MODEL_CARD.md` for the model design and its limits.
