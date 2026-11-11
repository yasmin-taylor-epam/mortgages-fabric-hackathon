# Alert threshold

**Status:** the rule and its target are a proposal. The team has not confirmed them. Synthetic data only. On 10 November 2026 notebook 05 evaluated the release with threshold 0.5062 in Fabric (see [evaluation-results.md](evaluation-results.md)).

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
| `min_precision` (**default**, target 0.6) | The lowest threshold at which at least 60% of the validation alerts are real leavers. | The target. 0.6 is a proposal. Change it with `HB_MIN_PRECISION` or `--min-precision`. |
| `max_f1` | The threshold with the best F1 on validation data (the best balance of precision and recall). | Nothing. |
| `min_cost` | The threshold with the lowest total cost on validation data: missed leavers times `cost_missed_leaver`, plus alerts times `cost_review`. | Both costs. There is no default. Set `HB_COST_MISSED_LEAVER` and `HB_COST_REVIEW`, or pass `--cost-missed-leaver` and `--cost-review`. |

**Why `min_precision` is the default.** A review case costs adviser time. The rule says: alert only where a leaver is clearly more likely than not, and alert on every account that meets that bar. The target 0.6 is 2.7 times the 22% base rate of leavers. The number of alerts is a result of the scores. It is not a fixed count, and it is not "the top N accounts". When more accounts score high, more alerts follow.

**What it costs.** The strict rule finds few leavers. On the test cohorts it finds about 10% of them (32 alerts, 66% precise). The best-balance rule (`max_f1`) finds 64%, with 32% precision. The strict rule is a choice for adviser time. It is not a claim that the other leavers do not matter.

**Limits of the rule.** The validation set has 460 accounts and 99 leavers. The strict rule rests on 16 validation alerts, so the exact threshold (0.5062) is noisy. The test cohorts give a check that does not depend on the choice: 65.6% precision on 32 alerts. If no threshold reaches the target, training stops and makes no release. It does not fall back to another number.

Choose the rule with `HB_THRESHOLD_RULE` or `--threshold-rule`. Notebook 03 has the parameters `THRESHOLD_RULE` and `MIN_PRECISION` (empty means the default).

## The options in plain numbers

From the local run on synthetic data (release `hb-refi-clf:1`). The test base rate is 22% leavers, so 22% is the precision of picking at random. Regenerate the table with `python -m models_eval evaluate`. It is in `data/output/reports/evaluation_report.md`.

"Checkpoint accounts alerted" counts the 303 open accounts at the 90-day checkpoint. "Live-style run" counts the same 303 accounts in a scheduled run at 15 October 2026 00:15 (the run that the live workspace made on 9 November). Scores differ between the two, because the model trains on the checkpoint and live runs score accounts on other days.

| Option | Threshold | Checkpoint accounts alerted | Live-style run alerted | Test recall | Test precision |
| --- | --- | --- | --- | --- | --- |
| `min_precision` 0.6 (**default**) | 0.5062 | 7 | 27 | 10% | 66% |
| `max_f1` (best balance) | 0.1982 | 141 | 161 | 64% | 32% |
| 80% recall on validation | 0.1286 | 225 | n/a | 84% | 26% |
| max F2 on validation | 0.0794 | 285 | n/a | 97% | 23% |
| top 10% of validation scores | 0.3669 | 34 | n/a | 25% | 45% |
| top 5% of validation scores | 0.4666 | 12 | n/a | 12% | 62% |
| retired: 10th-highest validation score | 0.5593 | 6 | 22 | 7% | 75% |

Recall is the share of real leavers that the alerts find. Precision is the share of alerts that are real leavers. A higher threshold gives fewer, more accurate alerts and misses more leavers.

The retired threshold 0.5593 came from an assumed review capacity of 10 cases per run. Nobody confirmed that capacity. The code no longer uses it. It is in the table for comparison only. The new default is close to it in effect (27 alerts in the live-style run, against 22). The difference is the reason: a stated precision target, not a count.

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
