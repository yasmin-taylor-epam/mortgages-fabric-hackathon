# pl_copy_context

**Owner:** Data and replay. **Blueprint step:** 7. **Status:** Design and local stand-in. Not yet built or run in Fabric.

Copies versioned account context and completed score summaries from the Lakehouse into Eventhouse. KQL joins these copies with accepted live events.

## Fabric design (proposed)

| Item | Setting |
| --- | --- |
| Trigger | After `01_validate_histories` succeeds, and after each completed scoring run. Proposed frequency to test: every 15 minutes. |
| Step 1 | Notebook activity: build `account_context` (see `data/contracts/account_context.schema.json`). |
| Step 2 | Copy activity: Lakehouse `account_context` → KQL table `AccountContext` (append; each copy has a new `context_version`). |
| Step 3 | Copy activity: completed-run scores → KQL table `ScoreContext`. Read only runs with `score_runs.status = completed`. |
| Step 4 | Copy activity: `feed_watermark` → KQL table `SourceWatermarks`. |

## Rules

- The account context holds no loan balance. Payment-support priority does not depend on balance or risk.
- `payment_support_status` is `unknown` when the servicing feed is stale.
- A score is `stale` in `ScoreContext` when a later scoring run failed (T05) or the score is older than 24 hours. The 24-hour limit is a proposed setting.
- Partial runs never reach `ScoreContext`.

## Local stand-in

```powershell
cd data
python -m hbdata copy-context
```

The local version writes `data/output/eventhouse/account_context.parquet`, `score_context.parquet` and `source_watermarks.parquet`. It reads scores from `data/output/lakehouse/gold/account_scores.parquet` and `score_runs.parquet`, which the Models and evaluation package writes.
