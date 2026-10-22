# KQL

Eventhouse table definitions, ingestion routing, journey queries and the live service view.

**Owner:** Data and replay work package.

**Status:** Written, not yet run. The Fabric workspace is not ready, and no Kusto emulator ran on the build machine. The pandas twins in `data/hbdata/` are tested instead.

## Files

Run the files in order on a new KQL database. Each file is one `.execute database script` command.

| File | Contents | Pandas twin |
| --- | --- | --- |
| `01_tables.kql` | `RawEventsLanding`, `RawEvents`, `QuarantinedEvents`, `AccountContext`, `ScoreContext`, `SourceWatermarks`, and the JSON mapping `RawEventsLanding_json`. | – |
| `02_ingest_routing.kql` | `AccountContextLatest()`, `LandingChecked()` and the update policies that route landing rows to `RawEvents` or `QuarantinedEvents`. | `replay.ingest_local` |
| `03_journeys.kql` | `TransferEventsAt(cutoff)`, `JourneyFlags(cutoff)` and the three journey queries. | `journeys.py` |
| `04_service_and_360.kql` | `ServiceEvents()` and `Customer360()`. | `replay.service_events` (no twin for `Customer360`) |

## Journey queries (blueprint section 03, step 6)

| Function | Question |
| --- | --- |
| `JourneyTimeline(cutoff)` | Each product-transfer request's stages and portal errors, in time order. |
| `StageDwell(cutoff)` | Days that a request stays at one stage. An open stage runs to the cutoff. |
| `RetriesAfterError(cutoff)` | Retries after a renewal-portal software error, and whether the request moved on. |

## Rules

- Each event keeps its original ID and timestamps. `ingested_at` is the Eventhouse ingestion time. A replay fixture can set `simulated_ingested_at` for repeatable tests.
- Journey results that feed model inputs use only events with `occurred_at` and `ingested_at` at or before the cutoff (INV-2).
- Queries deduplicate by `event_id` and keep the earliest ingestion (T01).
- An unknown account or a conflicting borrower mapping goes to quarantine (T02). Rejected events stay visible in `QuarantinedEvents`.
- An event that arrives before its account reaches `AccountContext` also goes to quarantine. Re-ingest it after the next context copy.
- `ServiceEvents()` emits `portal_fault_verified` only for a software fault with `fault_verified = true`. A rejected or abandoned request is not a portal fault.
- Keep each KQL function and its pandas twin in step. `tests/test_journeys.py` checks that both define the same output columns.
