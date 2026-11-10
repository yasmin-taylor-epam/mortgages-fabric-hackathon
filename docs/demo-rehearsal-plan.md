# Demo rehearsal: live part and reset (plan, not run)

**Status:** a plan, written on 10 November 2026. Nothing here has run. Every live step needs the user's approval. Guardrails: `prune` stays false. The Eventstream, the KQL database and the Eventhouse are not changed, except for the event rows that the rehearsal itself adds and step R3 removes. No demo profile. Synthetic data only.

**Clean state today (the state to return to):** `review_cases` 29 rows (27 renewal, 1 support HB-M-003124, 1 technical HB-M-900001), `review_decisions` 1,224 rows, `RawEvents` 21,533, `RawEventsLanding` 21,543, `QuarantinedEvents` 10, 0 events from the demo website.

## Live rehearsal

| # | Step | Needs approval | Takes |
| --- | --- | --- | --- |
| L0 | **Snapshot the clean state.** Copy `review_cases`, `review_decisions` and `review_routing_runs` to `_bak_20261110_clean29` tables (new suffix). Check the counts: 29, 1,224 and the current number of routing runs. Save the KQL baseline counts above and the `event_id` list of any event with `occurred_at >= 2026-10-16`. | yes | about 1.5 min (a Spark session takes 15 to 75 s to start) |
| L1 | **Start the portals** with the demo session's `serve_live.py` (port 8360), or the read-only launcher plus a POST-enabled copy. Open the customer site as Alex. | yes | 30 to 40 s warm-up |
| L2 | **Alex clicks "I need help with payments".** One `payment_support_requested` event (id `PSE-...`, source `servicing`) goes to `HomeBridgeRiskEventsV2`. The event time is the demo clock, 16 Oct 2026 09:00. | yes | the click is instant. The event reaches `RawEvents` in 15 to 45 s (the portal's arrival check shows it) |
| L3 | **Optional: "Send again"** (the duplicate test, T01). The same event goes in again. `RawEventsLanding` gets 2 copies. `RawEvents` keeps 1. The page shows "received 2 times". | yes | 15 to 45 s |
| L4 | **Staff portal: "Process new events now".** It runs notebook 07 with `AS_OF` 2026-10-16T09:00:00Z and no `EVENTS_BEFORE`. | yes | about 2 min (today's run took 2 min 4 s). The portal then re-reads the Lakehouse for 3 min because the SQL endpoint shows new rows about a minute late |
| L5 | **Check.** | no (read only) | 1 min |

**Expected result after L4:**

- `review_cases` = **30**: renewal 27 (ranks 1 to 27, unchanged), support **2** (HB-M-003124 and Alex, HB-M-900001 C02), technical 1.
- Alex's support case is at the top of the worklist, section 1 ("Asked for help with payments"), tagged "from the demo", opened 16 Oct 2026 09:00 plus the arrival delay. Alex's page shows the support case and the technical case. Alex still has no renewal case (score 38%, rank 56, below 51%).
- `review_decisions` about 1,225 rows (one more service event, one more `create_case` decision). It is overwritten on every run. `review_routing_runs` gets one more row (cases 30, service events 4).
- Summary page: "of them sent from the demo website" = 1 (2 with the duplicate test, if the page counts copies; the page marks "received 2 times").
- `RawEvents` 21,534. `RawEventsLanding` 21,544 (21,545 with L3). `QuarantinedEvents` 10.
- The Power BI report shows 30 cases only after a model refresh (about 15 s). The rehearsal does not need it.

## Reset to the clean state (before the real recording)

Always back up first. Use DELETE and targeted statements. Never DROP. Do not touch the Eventstream, its destination, its data connection or the mapping.

| # | Step | Needs approval | Takes |
| --- | --- | --- | --- |
| R0 | **Back up the rehearsal state** to `_bak_20261110_rehearsal` tables (`review_cases`, `review_decisions`, `review_routing_runs`) and save the rehearsal event rows to a file in the session folder. | yes | about 1.5 min |
| R1 | **Find the rehearsal events.** In KQL (read only): `RawEventsLanding \| where occurred_at >= datetime(2026-10-16) \| project event_id, event_type, mortgage_id, occurred_at, ingested_at` and the same on `RawEvents` and `QuarantinedEvents`. Expect only Alex's PSE event (plus any renewal event that someone sent). The synthetic history ends on 15 Oct 2026, so nothing else matches. | no | seconds |
| R2 | **Check the delete is allowed on the table.** `.show table RawEventsLanding policy` (read only). The same `.delete` worked on 9 November (event PSE-971883756). | no | seconds |
| R3 | **Delete the rehearsal events** from `RawEventsLanding`, `RawEvents` and, if present, `QuarantinedEvents`: `.delete table <T> records <\| <T> \| where event_id in ('<id>', ...)`, one table at a time. Use the exact event ids from R1. Then check the counts: 21,533 / 21,543 / 10. If a count differs, stop and report. | yes | under 1 min |
| R4 | **Remove the rehearsal case.** In Spark SQL (Livy): `DELETE FROM review_cases WHERE case_key = 'HB-M-900001\|C02\|support'` (check the key with a SELECT first). Expect 29 rows. | yes | about 1 min |
| R5 | **Restore the decisions and the routing runs.** `INSERT OVERWRITE TABLE review_decisions SELECT * FROM review_decisions_bak_20261110_clean29`. Delete the rehearsal row from `review_routing_runs` (`WHERE routed_at > <the clean run time>`). Expect 1,224 rows and the clean number of runs. | yes | 1 min |
| R6 | **Sync and refresh.** Refresh the SQL analytics endpoint, then the semantic model. Check: Alerts In Latest Run 27, Latest Model Version `hb-refi-clf:2`, Report As Of 16 Oct 2026 09:00, Score Age Minutes 0. | yes | about 1 min |
| R7 | **Local clean-up.** Delete `demo/portals/sent-times.local.json` (git ignores it). Restart the portals. The first event of the recording then gets 16 Oct 2026 09:00 again. | yes | seconds |
| R8 | **Verify the clean state.** Open the staff portal: 29 cases, support 1, renewal 27, technical 1. Alex has only the technical case. The summary shows 0 events from the demo website. The V2 Eventstream destination is Running. | no | 1 min |

## Notes

- The demo clock moves on by one hour per event. After the rehearsal's events are deleted and `sent-times.local.json` is removed, the next event is dated 16 Oct 2026 09:00 again (the clock reads the latest event in `RawEvents`). Check this in R8 by looking at the customer page text "The event time is the demo clock".
- `.delete` in Kusto is a soft delete. The rows stay in storage until the retention period and cannot be read. This is the same method as on 9 November.
- Not verified: the exact count on the summary page for the duplicate test, and that the model needs no refresh to show 30 cases.
