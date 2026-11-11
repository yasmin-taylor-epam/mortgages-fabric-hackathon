# Known limits and illustrative values

**Status:** all numbers here are illustrative synthetic-data values. The team has not confirmed them. Most limits come from a code review. Some were tested in a real Fabric workspace on 9 and 10 November 2026. The text says so where that happened. This page comes from a review of hidden limits. It says what each limit is, where it lives, and what was done.

## Fixed in code

| Limit | What changed |
| --- | --- |
| The alert threshold came from an assumed review capacity of 10 per run (0.5593). It caught about 7% of leavers on the test cohort. | The threshold now comes from a named rule saved with the model release. The default rule is a precision target of 60% on validation data (0.5062). See `docs/alert-threshold.md`. The test bound "at most 10 cases" is gone. |
| The 24-hour score age used the real clock. A replay a day after scoring gave 0 cases. | One as-of date (`HB_AS_OF`, `run.as_of`, pipeline parameter `as_of`). The case writer, the replay tools, the scoring run start, the pipeline cutoff, the report and `Customer360(as_of)` use it. With no as-of date they use the real clock, as before. |
| One failed run for one mortgage made all 303 scores stale. | Each run records the accounts it covers (`score_runs.scope_keys`). A failed run makes stale only the scores of those accounts. Old runs with no scope still cover every account. |
| A late servicing feed made every account "support unknown" and lost the known active-support case. | A known `active` status is kept. Accounts with no known case become `unknown`, and the router still holds their events. |
| The report measures and the KQL view used the real clock against data frozen on 15 Oct 2026. | They use the as-of date (see above). |
| `request_status` dropped an application with no matching "entered" row. | It uses a left join. Days at stage is blank for such a row. |
| The scoring pipeline had no retry on the snapshot and scoring notebooks. | One retry on each. The count is a placeholder. |
| The replay sent all events in one Event Hub batch. | It sends in order, in as many batches as the size limit needs. |
| The Eventstream destination used processed ingestion, which needs streaming ingestion. The deploy turns streaming off on purpose. | **Not fixed.** The repository definition now uses direct ingestion, but a live test on 9 November 2026 showed that Fabric rejected it (see below). |

## Open: the Eventstream destination is a manual portal step

Live results on 9 November 2026: the processed-ingestion destination failed; direct ingestion with the Eventhouse `itemId` failed at compatibility levels 1.0 and 1.1 (`UnsupportedArtifactType`, "is of type 'KustoEventHouse'"); direct ingestion with the KQL database `itemId` gave a "Warning" and no rows. Fabric also cannot upgrade an Eventstream from 1.0 to 1.1 in place. So no definition in this repository makes a working destination.

The portal flow worked once on a new Eventstream: delete a broken API-made destination, add an Eventhouse direct-ingestion destination, link the stream to it, publish, then use "Configure KQL data connection" in Live mode. Test events landed in 31 to 46 seconds. A full resend of 21,538 events landed in 15 to 30 seconds. The wizard needs sample events in the stream, and events sent before the data connection existed never landed. The steps are in `docs/fabric-deployment.md`, section "Eventstream destination: portal steps".

**Defect to check each time:** the wizard's "use existing mapping" list was empty, so it created the mapping `RawEventsLanding_mapping` instead of using `RawEventsLanding_json`. It mapped `$.schema_version` to a new column `schema_version_1` and added that column to the table. The existing `schema_version` column stayed empty in every row. Map it to the existing column in the wizard, or alter the mapping afterwards. The extra column can stay. It is unused, and dropping it is optional and needs the owner's approval. A fix was waiting for approval.

Still open: whether a redeploy removes the destination, and whether the flow works a second time.

## Open: "latest run" is chosen by completed_at

Notebook 07 (`latest_completed_run`) and the report measures pick the latest completed run by its `completed_at` or `scored_at`. A run that uses the as-of date (16 October 2026) has an earlier time than older runs that used the real clock (5 and 9 November). So after the first as-of run on a workspace with real-clock runs, the old run still looks "latest". On 10 November 2026 this was fixed by hand: the three old runs got new times just after their own cutoff (15 October 00:16 to 00:18), by timestamp updates only, with backups. Nothing was deleted. For a fresh workspace, run everything with `run.as_of` set from the start. Do not mix real-clock and as-of runs. A code fix (order by `cutoff_at` first) was not made.

## Kept on purpose (safety and data-quality rules)

These are rules INV-1 to INV-6 and cases T01 to T07 in `site/index.html`. Do not weaken them.

- Identity conflicts go to quarantine. Payment support comes first. A run that did not complete raises no case. A reversal is rechecked.
- Validation quarantines bad schema, duplicates, unknown links and invalid reversals. Rows stay visible in quarantine or audit tables.
- Unlabelled outcomes and leaked rows are excluded from training. Wealth-proxy and support features are excluded.
- A stale or missing payment feed is never shown as zero arrears (T03).
- The General viewer role hides payment-support cases. A judge who should see the support-first rule needs the Authorised adviser role.

## Placeholders to confirm with the team

- Maximum score age: 1,440 minutes.
- Feed stale limits: 24 hours (transfer), 72 hours (servicing, payment, contact), 730 days (account). No source is given for these numbers.
- Pipeline retry count: 1 for the two compute notebooks, 3 for publish. Timeouts: 20 minutes for compute, 10 for publish.
- Case writer: 3 attempts per write, then the error is recorded.
- Retrain cadence, the threshold rule and its precision target of 0.6 (`docs/alert-threshold.md`). The strict rule finds about 10% of leavers. A lower target (for example 0.5 gives 0.4036 and 45 live alerts) finds more.

## Documented limits (no change)

- **Checkpoint model, live use.** The model trains on the 90-day checkpoint. A live run scores accounts at other days before deal end. The same threshold gives 6 alerts at the checkpoint and 22 in one live run at the old threshold. The static explainer `docs/overview/homebridge-flow.html` still describes a "cap or separate live threshold". It is not edited. The decision is not to cap. If live scoring stays, validate a live threshold on live-style snapshots.
- **One request per account row.** The account view shows the latest transfer request per mortgage. Earlier or parallel requests are hidden from that row. Counts are not affected.
- **Evidence list.** A snapshot keeps the last 5 portal-fault event IDs. The most software errors in 90 days on any snapshot in the synthetic data is 4, so nothing is cut today.
- **Attributions.** Only the 10 largest feature contributions per account are stored. Scores do not change.
- **Deal window.** Live and replay windows end 30 days after the deal end. This matches the outcome window in `notebooks/models_eval/MODEL_CARD.md`. With a real-clock cutoff, accounts drop out as time passes. The as-of date prevents that.
- **Local flow.** With the as-of date at 2026-10-16T09:00:00Z, a local scheduled run still scores 303 accounts. The feed freshness check is made at load time (15 Oct 2026), not at the as-of time. At the as-of time the transfer feed would be older than its 24-hour limit.
- **Demo profile.** The example settings file has a `demo` environment with the as-of date and the schedule on. Every optional deploy step is otherwise off.
