# Fixtures

Small synthetic inputs and expected results for the failure cases in the blueprint (`site/index.html`). HomeBridge and every person here are fictional.

Rebuild after a generator or contract change:

```powershell
cd data
python -m hbdata fixtures
```

`tests/test_fixtures.py` fails if the committed files differ from the builder (`hbdata/fixtures.py`).

## Case files (`cases/`)

Each file has `inputs` (rows per dataset), optional `live_events` (live-event envelopes), `expected` and `behaviour_owner`. Mini fixtures use mortgage IDs `HB-M-8xxxxx`, so they never clash with generated data.

| File | Case | What Data and replay checks | Behaviour also owned by |
| --- | --- | --- | --- |
| `T01.json` | The same eligible event is replayed. | One service event and one journey row per `event_id`. | Dashboard: one review case per key. |
| `T02.json` | Unknown account or conflicting borrower mapping. | Source records and live events go to quarantine. `identity_status = conflict`. | – |
| `T03.json` | Payment feed stale or missing. | `ctx_payment_feed_stale = true` and null arrears. A missing feed is not zero arrears. | Dashboard: show the stale flag. |
| `T04.json` | A payment is reversed. | Both ledger rows stay. An invalid reversal goes to quarantine. A live reversal becomes `payment_reversed`. | Dashboard: flag open cases for a recheck and record `rescore_requested`; no new case from the reversal alone. Models: rescore. |
| `T05.json` | A scoring run fails. | `ScoreContext` keeps the last completed score, marked `stale`. Service events continue. | Models: publish no `risk_updated` for the failed run. |
| `T06.json` | A review-case write fails. | The `risk_updated` example meets its contract. | Dashboard: retry with the same key; one case. |
| `T07.json` | Three contacts about one unresolved problem; three contacts about separate enquiries. | Contacts per case and repeat contacts differ. A state change is not a contact. | – |
| `CUT01.json` | INV-2 cutoff check. | Rows ingested after the cutoff and late resolutions are not inputs. | – |

## Demo replay (`replay/`)

These continue the generated persona Alex (`HB-M-900001`) after the generator `as_of` time.

| File | Content |
| --- | --- |
| `demo_alex.jsonl` | A retry, a verified renewal-portal fault (`PTE-900000002`) and a payment-support request. |
| `demo_alex_duplicate.jsonl` | The same fault event sent again (T01). |

```powershell
cd data
python -m hbdata all
python -m hbdata replay --fixture fixtures/replay/demo_alex.jsonl
python -m hbdata replay --fixture fixtures/replay/demo_alex_duplicate.jsonl
python -m hbdata export-journeys --at 2026-10-16T12:00:00Z
python -m hbdata snapshots --live --at 2026-10-16T12:00:00Z --mortgage HB-M-900001
```

To send a fixture to a Fabric Eventstream custom endpoint, set `HB_EVENTSTREAM_CONNECTION` and `HB_EVENTSTREAM_NAME` in your shell, install `azure-eventhub`, and add `--target eventstream`. Never commit those values. This path has not been run yet.
