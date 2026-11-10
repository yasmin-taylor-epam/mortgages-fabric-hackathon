# Live deploy plan: hidden-limits fixes and the strict threshold

**Status:** a plan, not run. Written on 10 November 2026. Every step needs approval before anyone runs it. Nothing here has run in Fabric. The numbers come from the local twin of the data (same seed, same code), which matched the live run on 9 November (161 alerts at 0.1982, 22 at 0.5593).

Workspace: `azure-mafia-branch-luke`. The demo session also works in this workspace. Do not run two steps at the same time.

## What changes in the workspace

| Item or file | Change | Step |
| --- | --- | --- |
| `Files/hb/code/...` (models_eval, hbdata, action, review_routing, policy template) | New code. The policy template has **no threshold** now. | 3 |
| Notebooks 03, 04, 06 (changed), 07 (from the demo branch) | Notebook 03 gets `THRESHOLD_RULE` and `MIN_PRECISION`. Notebook 04 gets the as-of clock. Notebook 06 gets `AS_OF` and creates `review_outcomes` (empty) and `report_settings`. | 4 |
| Pipeline `pl_scheduled_scoring` | New parameter `as_of`, retry 1 on two notebooks. | 4 |
| Model release | A new release (`hb-refi-clf:2`) with the rule `min_precision` 0.6. | 5 |
| `score_runs` (Delta table) | New column `scope_keys`. Old rows keep it empty and still cover every account. | 5, 6 |
| Semantic model and report | New tables `review_outcomes`, `report_settings`. New column `score_runs[scope_keys]`. Measures read `Report As Of`. | 6 |
| `review_cases`, `review_decisions` | Backed up, cleared, rebuilt by notebook 07. | 7, 8 |

## What must not change

The Eventstream `HomeBridgeRiskEventsV2`, its Eventhouse destination and its KQL data connection (made in the portal), and the Eventhouse and KQL database.

How the plan keeps away from them:

- The `core` step runs with `skip_item_types: [Eventstream, KQLDatabase, Eventhouse]`. Fabric-cicd then does not publish those types and the script does not read the Eventstream. The KQL script (and so the Kusto table, the wizard mapping and the extra column `schema_version_1`) is not touched. The only KQL change in this work (`Customer360(as_of)`) is not needed for the demo. Apply it later, with its own approval.
- The steps `eventstream`, `replay`, `streaming`, `retention` and `capacity` are not in the run. The script never lists them by default.
- `prune` stays `false`, so nothing is deleted. The portal-made Eventstream is not in the repository, so a prune run would delete it. Never set `prune: true` here.
- The dry run (step 2) prints the item types in scope. Check that the three types are missing from the list before step 4.
- Not verified: that fabric-cicd leaves a skipped type alone in a workspace that holds items of that type. The tests check the written scope file with the fabric-cicd validator. They do not run Fabric.

## Settings for this workspace (in the Git-ignored `fabric/config/fabric.config.yml`)

```yaml
skip_item_types: [Eventstream, KQLDatabase, Eventhouse]
prune: false
run:
  as_of: "2026-10-16T09:00:00Z"   # the demo clock
```

Leave every optional section off (`schedule`, `roles`, `rls`, `retention`, `replay`, and the others). Names only. No credentials.

## Steps

1. **Merge the code (no Fabric).** Merge this branch into `main` first. Then merge the demo branch (`luke-harjulin-epam-design-tangible-demo`). A trial merge gave no conflicts, and all suites passed on the merged tree. Deploy from the merged commit. *Approval: the person who owns `main`.*
2. **Offline check, then dry run.** `powershell -File scripts\deploy_fabric.ps1 --validate`, then `... --dry-run --only upload core notebooks pipeline model`. The dry run reads Fabric and changes nothing. Check: the `core` line says it leaves Eventstream, KQLDatabase and Eventhouse alone. *Approval: to sign in.*
3. **Upload the code.** `--only upload`. It writes `Files/hb/code` and the synthetic exports. It does not touch tables, models or the Eventstream. Check that no `Files/hb/output/policy/routing-policy.json` exists. A working policy file in that place wins over the release, and nobody refreshes it. If one exists, ask before removing it. *Approval: to write files.*
4. **Publish the notebooks and the pipeline.** `--only core` (with the skip setting). Items published: Lakehouse (unchanged), Environment (unchanged), notebooks 00 to 07, pipeline `pl_scheduled_scoring`. *Approval: to publish items.*
5. **Retrain and score.** `--only notebooks pipeline`. This runs notebooks 00 to 06 in order, then the pipeline once with `as_of` 2026-10-16T09:00:00Z, then notebook 06 again.
   - Notebook 03 retrains with the default rule. **Gate:** read the new release. Expected: rule `min_precision`, target 0.6, threshold 0.5062. Fabric library versions may move it a little. Stop if the threshold is far from 0.5062 or if the live run has fewer than 20 or more than 30 alerts.
   - Notebook 04 (checkpoint run) adds one more checkpoint run with the new model. Expected local result: 303 accounts, 7 alerts above 0.5062.
   - The pipeline run is the new latest run. Expected: run `RUN-20261016-090100`, 303 accounts, **27 alerts with priority ranks 1 to 27**, started 09:01 (one minute after the as-of time).
   - Alex (`HB-M-900001`): score 0.393, rank 50 of 303, **below** the bar. Alex gets no renewal case.
   - Tables written by notebook 06: `score_runs` (with `scope_keys`), `account_scores`, `report_settings` (one row, `as_of` 2026-10-16 09:00), `review_outcomes` (empty, created only when missing).
   - *Approval: to run notebooks and the pipeline (about the cost of the 9 November run, longer for the retrain).*
6. **Publish the semantic model and the report.** `--only model`. It refreshes the SQL analytics endpoint, publishes the model and the report, then refreshes the model. Do this only after step 5, because the model needs the new tables. **Role members:** a model publish writes the roles with no members. Note who is in `General viewer` and `Authorised adviser` before, and add them again after (the `rls` step or the portal). Check: the report shows `Alerts In Latest Run` 27, and the pages load. *Approval: to publish the model and the report.*
7. **Back up and clear the review cases.** In a scratch notebook that is attached to the Lakehouse, copy `review_cases` and `review_decisions` to `review_cases_bak_20261110` and `review_decisions_bak_20261110` (`CREATE TABLE ... AS SELECT *`). Check the row counts match. Then `DELETE FROM review_cases`. Never `DROP`, so the columns that notebook 07 added stay. Anything made by the demo portals before this step is in the backup only, and is not rebuilt (see the next step). *Approval: separate for the backup and for the delete. Coordinate with the demo session first.*
8. **Rerun the case routing.** Run notebook 07 with `AS_OF` = `EVENTS_BEFORE` = `2026-10-16T09:00:00Z`, and `KUSTO_URI` and `KUSTO_DB` set as the demo session sets them. It reads the new release as the threshold (the policy template has none). Check that its first print line says the policy came from the model release, with threshold 0.5062. Expected counts from the local twin:

   | Result | Expected |
   | --- | --- |
   | `risk_updated` events from the latest run | 303 |
   | Cases in `review_cases` | **29** |
   | Renewal cases, ranks 1 to 27 | 27 |
   | Support case (the one account with active payment support, shown at any score) | 1 |
   | Technical case (Alex's unresolved portal fault) | 1 |
   | Rejected events and exceptions | 0 and 0 |
   | `review_decisions` rows | about 1,224: 275 `below_threshold`, 27 eligible, 1 support first, 3 verified fault (one case), 918 faults left out (909 recovered, 8 closed, 1 deal ended) |

   The live numbers may differ a little, because the live Eventhouse has the demo events and a few duplicate copies. Events after 09:00 are cut by `EVENTS_BEFORE`. *Approval: to run the notebook.*
9. **Check and record.** Count the rows in `review_cases` by family. Open the report and the portals. Write what was checked, and what failed, into `docs/fabric-deployment.md`.

## Undo

- Steps 3 to 6 are repeatable. A wrong release or run can be left behind: the newest completed run wins, and `model_version` can be pinned in the pipeline parameters.
- Step 8: restore from the backup tables with `INSERT INTO review_cases SELECT * FROM review_cases_bak_20261110` after clearing again.
- The old threshold 0.5593 is not restorable by a setting. Train with `THRESHOLD_RULE = max_f1` for the best-balance threshold, or give `MIN_PRECISION` a different target.

## Not covered

The retention step, the 15-minute schedule (the `demo` profile turns it on, so do not use that profile here), roles, the Eventstream and the data connection, and `Customer360(as_of)`. All need their own approval.
