# Notebooks

Fabric notebooks that validate data, build feature snapshots, train models, score accounts and evaluate results.

Store notebooks as `.ipynb` files. The team decides on Fabric Git integration when the workspace is ready.

## Proposed notebooks

The names are proposals. The blueprint (`site/index.html`, section 03) defines the steps.

| Notebook | Blueprint step | Owner |
| --- | --- | --- |
| `01_validate_histories` | Step 2: validate keys and ledger history. Build clean account histories. | Data and replay. Built; runs locally. |
| `02_feature_snapshots` | Step 2: build frozen feature snapshots at each checkpoint. | Data and replay. Built; runs locally. |
| `03_train_models` | Step 3: train the classifier, the rules baseline and the clusters. Record them in MLflow. | Models and evaluation |
| `04_score_and_segment` | Steps 4 and 9: score accounts, assign segments and write the completed-run marker. | Models and evaluation |
| `05_evaluate` | Section 04: later-cohort evaluation report. | Models and evaluation |

## Rules

- The Data and replay notebooks call the `hbdata` package in `data/`. Models notebooks can call `hbdata.snapshots.build_snapshots` for live scoring snapshots, so that training and scoring use the same feature code.
- INV-2: features use only data known at the checkpoint. Both `occurred_at` and `ingested_at` must be at or before `cutoff_at`.
- INV-4: an unknown or incomplete refinancing outcome gets no model label. It never counts as retained.
- INV-5: `risk_updated` fires only after a scoring run succeeds. A failed run leaves the last score marked stale.
- Append one segment row per scheduled run. Never overwrite a segment row.
- Report precision, recall, calibration and lift at ten reviews per run. Compare each model with the rules baseline.
