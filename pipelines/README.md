# Pipelines

Fabric pipeline definitions. Export each definition here as JSON when the workspace is ready.

## Proposed pipelines

The names are proposals. The blueprint (`site/index.html`, section 03) defines the steps.

| Pipeline | Blueprint step | Owner |
| --- | --- | --- |
| `pl_load_raw` | Step 1: load dated exports into raw Lakehouse tables. | Data and replay. Design and local stand-in in [pl_load_raw/](pl_load_raw/README.md). |
| `pl_copy_context` | Step 7: copy account context and completed score summaries into Eventhouse. | Data and replay. Design and local stand-in in [pl_copy_context/](pl_copy_context/README.md). |
| `pl_export_journeys` | Step 8: copy cutoff-filtered KQL journey results into Lakehouse staging tables. | Data and replay. Design and local stand-in in [pl_export_journeys/](pl_export_journeys/README.md). |
| `pl_scheduled_scoring` | Step 9: run scoring and segment assignment on a schedule. | Models and evaluation. Draft definition and local runner in [pl_scheduled_scoring/](pl_scheduled_scoring/README.md). |

## Rules

- The proposed scoring interval is 15 minutes. It is a target to test, not a platform guarantee.
- A scoring run does not retrain the models.
- Never put credentials or connection secrets in a pipeline definition.
