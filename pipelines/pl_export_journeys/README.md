# pl_export_journeys

**Owner:** Data and replay. **Blueprint step:** 8. **Status:** Design and local stand-in. Not yet built or run in Fabric.

Copies cutoff-filtered KQL journey results into Lakehouse staging tables. Notebooks combine these results with dated history.

## Fabric design (proposed)

| Item | Setting |
| --- | --- |
| Trigger | Called by `pl_scheduled_scoring` before each scoring run, with the run's `cutoff_at`. |
| Parameter | `cutoff_at` (datetime, UTC). |
| Activities | Three Copy activities. Source: KQL database. Queries: `JourneyTimeline(datetime(@{cutoff_at}))`, `StageDwell(...)`, `RetriesAfterError(...)`. |
| Sink | Lakehouse tables `stg_journey_timeline`, `stg_stage_dwell`, `stg_retries_after_error`. Overwrite per `export_id`. |
| Added columns | `export_id`, `cutoff_at`, `source_watermark`, `exported_at`. |

The KQL query and the notebook apply the same cutoff (INV-2). Both `occurred_at` and `ingested_at` must be at or before `cutoff_at`.

## Local stand-in

```powershell
cd data
python -m hbdata export-journeys --at 2026-10-16T12:00:00Z
```

The local version runs the pandas twin of the KQL (`data/hbdata/journeys.py`) on the local Eventhouse folder. It writes `data/output/lakehouse/staging/*.parquet` and `_export.json`.

Capability reference: [KQL database copy activity](https://learn.microsoft.com/en-us/fabric/data-factory/connector-kql-database-copy-activity). This describes a capability, not access in the team's tenant.
