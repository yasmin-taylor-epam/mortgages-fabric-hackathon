# Model card: HomeBridge refinancing risk (`hb-refi-clf`) and segments (`hb-segments`)

**Status:** Prototype. Trained and tested locally on synthetic data only. Not run in Fabric. HomeBridge is a fictional mortgage lender.

## Purpose

The model scores the risk of confirmed external refinancing for one mortgage deal cycle. The checkpoint is 90 days before the fixed-rate deal ends. A servicing adviser uses the score to decide which accounts to review for renewal.

The score does not approve a mortgage, set a rate or permit customer contact (INV-6). Payment-support requests go to trained staff first, whatever the score (INV-1). Payment support does not depend on this model.

Out of scope: mortgage default prediction, lending decisions and product recommendations.

## Target

- Positive (1): confirmed external refinancing between the checkpoint and 30 days after the deal ends.
- Negative (0): a same-lender product transfer, or continued servicing. This label is set only after the observation window ends.
- No label: sales, bereavement closures, redemptions with no known reason, and outcomes not yet observed. These never count as retained (INV-4).

## Inputs

The inputs are frozen feature snapshots (`feature_version` `fv1`). They hold only data whose event time and ingestion time are at or before the cutoff (INV-2).

- Product-transfer request: whether a request started, its latest stage and status, days at the current stage, failed attempts in the last 30 and 90 days, software errors, retries after an error, abandoned or rejected requests.
- Servicing: cases by category, unresolved cases and complaints at the cutoff, complaint age, contacts in the last 30 and 90 days, contact trend, contacts per case, repeat contacts about unresolved problems.
- Deal terms: the follow-on rate gap and the deal length.

These inputs are excluded on purpose:

- Payment and routing context (`ctx_` columns).
- `days_to_deal_end`. It is always 90 at a checkpoint.
- Loan balance and payment amount.
- Sensitive support details, names, protected characteristics and fine-grained location. These are not in the snapshot.

## Models

| Model | Role | Settings frozen on validation data |
| --- | --- | --- |
| Rules baseline | Product-transfer request friction. Each of four rules adds 0.25. | Stall-day limit |
| Logistic regression | Learned model, standardised inputs | Regularisation C |
| Decision tree | Learned model, depth 3 or 4, at least 25 accounts per leaf | Depth |

The champion is the learned model with the higher validation average precision. Logistic regression wins a near tie. Each model has an alert threshold candidate that maximises F1 on validation data. The release also records a capacity threshold: the score of the tenth-highest validation account. The team confirms the routing threshold. The blueprint does not specify it.

Clusters: k-means on request and servicing behaviour, with k from 2 to 5. The selected k has the best silhouette among the stable solutions. A stable solution has a mean bootstrap adjusted Rand index of at least 0.8 and a worst index of at least 0.5. The clusters are fitted once per release, and each run only assigns accounts. A segment ID keeps its meaning until the next release. Segment names come after the team inspects the profiles.

## Evaluation design

- Cohorts are deal-end months. The simulated release is on 1 June 2026. Training uses 2026-01 to 2026-03, validation uses 2026-04 and the test uses 2026-05 and 2026-06.
- Training and validation labels must be observed before the release. Test-cohort borrowers are removed from training.
- Metrics: accuracy, precision, recall, ROC AUC, average precision, Brier score, expected calibration error, a calibration table, and lift at ten reviews per run. Lift is computed for each test cohort and has a bootstrap interval.

## Results on synthetic data (illustrative)

These results come from the Data and replay generator with its default settings (seed 20261026). They show that the workflow works. They do not show real predictive power or improved retention. Run `05_evaluate` to reproduce them.

| Model | Avg precision | ROC AUC | Brier | Mean lift at 10 |
| --- | --- | --- | --- | --- |
| Rules baseline | 0.225 | 0.509 | 0.214 | 0.98 |
| Logistic regression (champion) | 0.409 | 0.672 | 0.159 | 3.38 |
| Decision tree | 0.324 | 0.653 | 0.164 | 2.68 |

The test base rate is 0.222. Ten accounts is a small sample. The 95% interval for the champion's lift at ten is about 1.8 to 4.6 in each cohort.

## Explanations

Each score has per-account attributions. For logistic regression, an attribution is the coefficient times the standardised value, in log-odds. For the decision tree, it is the SHAP value, in probability. An attribution explains the score. It does not prove the borrower's motive or show that an action will work.

## Limits and risks

- The labels come from hidden factors in a synthetic generator. Real refinancing behaviour will differ.
- The proxy check covers only the context that is available: payment support, missed payments and loan balance. A real deployment needs a fairness review with suitable protected-characteristic data and lawful processing.
- The scores are calibrated on the synthetic test cohorts. Check calibration again on real data before a threshold is used.
- Before real use, get lending, privacy and responsible-AI review. Confirm the jurisdiction, the contact policy and the fairness checks.
