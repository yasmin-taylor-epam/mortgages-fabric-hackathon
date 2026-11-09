# Alert threshold

**Status:** the rule is a proposal. The team has not confirmed it. Synthetic data only. Not run in Fabric.

## What the threshold does

An account gets a review case when its score is at or above the alert threshold. Every such account gets a case. There is no cap. The score rank (`priority_rank`, 1 is the highest score) only orders the review queue.

## Where the number comes from

Nobody types the threshold in. The model release stores it, with the name and the parameters of the rule that chose it.

| Step | What happens |
| --- | --- |
| Train | `python -m models_eval train` chooses the threshold on validation data with a named rule. The release stores `alert_threshold`, `alert_threshold_rule` and `alert_threshold_params`. |
| Routing policy | `python -m models_eval policy` copies the value into `data/output/policy/routing-policy.json`. `action/policy/routing-policy.candidate.json` is only a template and has no threshold. |
| Scoring | Notebook 04 and `models_eval pipeline` read the same value from the release. |
| Activator | It sends every event to the flow, with no score filter. The router logs each outcome, including `below_threshold`. |

The rules:

| Rule | How it chooses | Needs from the team |
| --- | --- | --- |
| `max_f1` (default) | The threshold with the best F1 on validation data. | Nothing. |
| `min_cost` | The threshold with the lowest total cost on validation data: missed leavers times `cost_missed_leaver`, plus alerts times `cost_review`. | Both costs. There is no default. Set `HB_COST_MISSED_LEAVER` and `HB_COST_REVIEW`, or pass `--cost-missed-leaver` and `--cost-review`. |

Choose the rule with `HB_THRESHOLD_RULE` or `--threshold-rule`.

## The options in plain numbers

From the local run on synthetic data (release `hb-refi-clf:1`). The test base rate is 22% leavers, so 22% is the precision of picking at random. "Open accounts alerted" counts the 303 open accounts at the checkpoint. Regenerate the table with `python -m models_eval evaluate`. It is in `data/output/reports/evaluation_report.md`.

| Option | Threshold | Open accounts alerted (of 303) | Test recall | Test precision |
| --- | --- | --- | --- | --- |
| `max_f1` (default rule) | 0.1982 | 141 | 64% | 32% |
| 80% recall on validation | 0.1286 | 225 | 84% | 26% |
| max F2 on validation | 0.0794 | 285 | 97% | 23% |
| top 10% of validation scores | 0.3669 | 34 | 25% | 45% |
| top 5% of validation scores | 0.4666 | 12 | 12% | 62% |
| retired: 10th-highest validation score | 0.5593 | 6 | 7% | 75% |

Recall is the share of real leavers that the alerts find. Precision is the share of alerts that are real leavers. A higher threshold gives fewer, more accurate alerts and misses more leavers.

The retired threshold 0.5593 came from an assumed review capacity of 10 cases per run. Nobody confirmed that capacity. The code no longer uses it. It is in the table for comparison only.

## Adviser outcomes (read only)

The semantic model has a table `review_outcomes` for adviser verdicts: `case_key`, `decision` (`pending`, `action_agreed`, `no_action`, `referred`), `decided_at` and `decided_by` (an adviser ID, not a login name). Notebook 06 creates it empty if it is missing. Nothing writes to it yet. It is not the routing table `review_decisions` that notebook 07 writes.

The measure `Alert Precision (Adviser Outcomes)` is `action_agreed` divided by (`action_agreed` plus `no_action`) for renewal cases. It is blank until a case is decided. Referred and pending cases are counted on their own measures.

- "Agreed" is the adviser's opinion. It is not a confirmed refinancing.
- Nothing retunes the threshold from this number. A person decides.

## Retrain cadence (proposal)

Retraining stays manual. Proposed, not agreed:

- Retrain and re-run `models_eval policy` every month, or sooner when the adviser precision stays below a level that the team sets.
- Review the options table at each retrain, and choose the rule again if the review capacity or the costs change.
- Keep the old release. The policy file names the release that it came from.

## What is still open

- The team has not confirmed the rule, the costs or the retrain cadence.
- The maximum score age (`max_score_age_minutes`, 1,440) is a placeholder. The team has not confirmed it either.
