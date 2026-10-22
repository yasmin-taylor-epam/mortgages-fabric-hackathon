# pl_scheduled_scoring

**Owner:** Models and evaluation. **Blueprint step:** 9. **Status:** Draft definition and local runner. Not yet built or run in Fabric.

Runs scoring and segment assignment on a schedule for the fictional lender HomeBridge. For the replay demo, the team also starts one run by hand (`run_kind = manual`). Synthetic data only.

## Fabric design (proposed)

`pipeline.json` is a hand-written draft for review. It is not a Fabric export. Replace the placeholder notebook and workspace IDs after import.

| Item | Setting |
| --- | --- |
| Trigger | Schedule. The proposed interval is 15 minutes. It is a target to test, not a platform guarantee. Set the schedule on the pipeline item in Fabric. |
| Before this pipeline | `pl_export_journeys` (Data and replay) stages cutoff-filtered journey results. |
| Activity 1 | Notebook `02_feature_snapshots` (Data and replay). `MODE = live` at the cutoff, or `checkpoint` for a checkpoint run. |
| Activity 2 | Notebook `04_score_and_segment`. Pinned `MODEL_VERSION` and `CLUSTER_MODEL_VERSION`. `PUBLISH = False`. No retry: a failed run stays failed. |
| Activity 3 | Notebook `04_score_and_segment` with `PUBLISH_ONLY = True`. Runs only after activity 2 succeeds. Retries three times. |
| After this pipeline | `pl_copy_context` (Data and replay) copies completed score summaries into Eventhouse. |

Parameters: `run_kind` (`scheduled`, `checkpoint` or `manual`), `cutoff_at` (default: trigger time), `model_version`, `cluster_model_version` (default: latest release).

## Rules

- A scoring run does not retrain the models. It loads pinned MLflow versions.
- INV-2: the scoring notebook stops if a snapshot holds data after its cutoff.
- INV-5 / T05: a failed scoring activity marks the run `failed`. It writes no `risk_outbox` rows. Activity 3 does not run, so no `risk_updated` event fires. The last completed score shows as stale.
- A publication retry resends the stored payload with the same `event_id`. It never rescores.
- Segment rows are appended once per run. Earlier rows never change.
- Never put credentials or connection secrets in a pipeline definition. The Eventstream connection is not built yet. Locally, events go to `data/output/eventstream/risk_updated.jsonl`.

## Local runner

The local runner performs the three activities in order on Parquet folders.

```powershell
cd notebooks
python -m models_eval pipeline --cutoff 2026-10-15T00:00:00Z --kind checkpoint --started-at 2026-10-15T00:05:00Z
python -m models_eval pipeline --cutoff 2026-10-15T00:15:00Z --kind scheduled --started-at 2026-10-15T00:16:00Z
# T05 demo: the run fails after writing outputs. No risk_updated event fires.
python -m models_eval pipeline --cutoff 2026-10-15T00:30:00Z --kind scheduled --started-at 2026-10-15T00:31:00Z --inject-failure after_outputs
# Retry pending publication only.
python -m models_eval publish
```

See `notebooks/models_eval/README.md` for setup.
