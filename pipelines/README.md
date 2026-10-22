# Pipelines

Fabric pipeline definitions. Export each definition here as JSON when the workspace is ready.

## Proposed pipelines

The names are proposals. The blueprint (`site/index.html`, section 03) defines the steps.

| Pipeline | Blueprint step | Owner |
| --- | --- | --- |
| `pl_load_raw` | Step 1: load dated exports into raw Lakehouse tables. | Data and replay |
| `pl_copy_context` | Step 7: copy account context and completed score summaries into Eventhouse. | Data and replay |
| `pl_export_journeys` | Step 8: copy cutoff-filtered KQL journey results into Lakehouse staging tables. | Data and replay |
| `pl_scheduled_scoring` | Step 9: run scoring and segment assignment on a schedule. | Models and evaluation |

## Rules

- The proposed scoring interval is 15 minutes. It is a target to test, not a platform guarantee.
- A scoring run does not retrain the models.
- Never put credentials or connection secrets in a pipeline definition.
