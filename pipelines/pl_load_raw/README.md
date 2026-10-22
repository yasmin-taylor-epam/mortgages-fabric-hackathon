# pl_load_raw

**Owner:** Data and replay. **Blueprint step:** 1. **Status:** Design and local stand-in. Not yet built or run in Fabric.

Loads dated HomeBridge (fictional) exports into raw Lakehouse tables. Raw tables are append-only. A load never overwrites an earlier load.

## Fabric design (proposed)

| Item | Setting |
| --- | --- |
| Trigger | Daily schedule, plus manual run for the demo. |
| Source | Dated export files in the Lakehouse `Files/exports/<yyyy-mm-dd>/` folder. One Parquet file per dataset. |
| Activity | One Copy activity per dataset, inside a ForEach over the dataset list. |
| Sink | Lakehouse Delta table `raw_<dataset>`, append mode. |
| Added columns | `_load_id`, `_loaded_at`, `_source_file`. |
| Skip rule | Skip a file whose hash is already in `raw__loads`. |
| Next step | Run notebook `01_validate_histories`. |

Datasets: `mortgage_account`, `account_party`, `payment`, `transfer_request_event`, `servicing_case_event`, `servicing_contact`, `observed_outcome`. The schemas are in `data/contracts/`.

## Local stand-in

```powershell
cd data
python -m hbdata generate
python -m hbdata load-raw
```

The local version writes `data/output/lakehouse/raw/<dataset>/<load_id>.parquet` and `raw/_loads.json`. It keeps the contract columns only and records the load metadata in `_loads.json`.

## Rules

- Never put credentials or connection secrets in the pipeline definition.
- Export the Fabric pipeline JSON to this folder when the workspace is ready.
