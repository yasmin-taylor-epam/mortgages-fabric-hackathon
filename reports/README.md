# Reports

The Power BI mortgage report and its semantic model.

**Owner:** Dashboard and action work package.

**Status:** The report rules run and pass tests locally. The semantic model passes the Microsoft TOM deserialiser. The report files pass the published Microsoft JSON schemas. Nobody has opened the project in Power BI Desktop or deployed it to Fabric. Nobody has run the DAX measures yet. The Fabric workspace is not ready.

## Contents

| Path | Contents |
| --- | --- |
| `HomeBridgeMortgage.pbip` | Power BI project entry file. |
| `HomeBridgeMortgage.SemanticModel/` | Semantic model in TMDL. Direct Lake on the Gold Lakehouse tables. 26 measures, 11 relationships and 2 roles. |
| `HomeBridgeMortgage.Report/` | Report in PBIR format. Four pages: Account view, Support history, Segment history, Data freshness. |
| `logic/gold_schema.sql` | The Gold table shapes that the report reads. The producers own the real schemas. See `../action/contracts/consumer-contract.md`. |
| `logic/views.sql` | Reference report rules as DuckDB table macros. The DAX measures copy these rules. |
| `tests/` | Tests for the rules and static checks for the project files. `tests/fixtures/` holds hand-written synthetic rows. |
| `tools/tmdl-check/` | A .NET tool that loads the model with the Microsoft TOM library. |

## Report pages

| Page | Shows |
| --- | --- |
| Account view | Fixed-rate deal end, checkpoint date, current terms, score, score status and age, segment, request stage and days at stage, verified portal faults, payment-support priority, review owner, payment position and feature attributions. |
| Support history | Each source case with enquiry type, support route, status, resolution and contacts per case. Totals of unresolved cases and repeat contacts. |
| Segment history | The segment of each account on a chosen date, and the append-only segment rows. |
| Data freshness | Payment feed state, last scoring run status, feed watermarks and scoring runs. |

## How the rules work

| Rule | Measure | Reference macro | Tests |
| --- | --- | --- | --- |
| INV-5, T05: a failed latest run marks the last good score stale. Partial rows from a failed run never show. | `Score Status` | `latest_score` | `TestScoreAgeAndStaleness` |
| T03: a missing or stale payment feed shows as missing or stale, never as zero arrears. | `Payment Feed State`, `Arrears`, `Payment Position` | `payment_position` | `TestT03PaymentFeed` |
| T04: a reversal keeps the original row. The position nets the two rows. | `Reversals`, `Arrears` | `payment_position` | `TestT03PaymentFeed` |
| T07: contacts count each `contact_id` once. State changes are not contacts. Only `resolved` is resolved. | `Contacts`, `Case Resolution`, `Repeat Contacts On Unresolved` | `support_history`, `support_summary` | `TestT07SupportHistory` |
| Segment on any past date. Rows are append-only. | `Segment As Of` | `segment_as_of`, `segment_conflicts` | `TestSegmentHistory` |
| Days at the current request stage. Rejection is not a portal fault. | `Current Request Stage`, `Days At Stage`, `Verified Portal Faults` | `request_status` | `TestRequestStatus` |
| INV-1: payment-support priority shows whatever the score or balance. | `Payment Support Priority` | `account_view` | `TestAccountView` |

`Max Score Age Minutes` is 1440. This is an illustrative value. Agree the real value with the Models and evaluation work package.

## Access and sensitivity labels

- The `General viewer` role hides payment-support cases, their contacts and support review cases. The `Authorised adviser` role sees them. Assign members in the Fabric service.
- The model holds routing information only. It holds no case notes.
- Apply a Microsoft Purview sensitivity label to the semantic model and the report in the Fabric service. Labels are service metadata. They are not in the TMDL or PBIR files. Confirm the label name with the tenant administrator.
- The acceptance test needs a label on every Fabric item that holds personal data (blueprint section 06).

## Run the tests

```powershell
python -m venv .venv
.venv\Scripts\pip install -r reports\requirements.txt jsonschema
.venv\Scripts\python -m pytest reports
```

The schema test downloads the Microsoft JSON schemas from `github.com/microsoft/json-schemas`. It skips when the network is not available. Set `HOMEBRIDGE_SCHEMA_CACHE` to choose the cache folder.

`tests/test_report_data_integration.py` checks the report against the Data and replay package (PR #8):

- Every column that the report reads exists in the producer contract in `data/contracts/`.
- The T03 fixture scenarios (missing, stale and fresh payment feed) give the expected feed state and arrears.
- The report rules run on the generated tables in `data/output/lakehouse/clean/`. Contacts per case and resolution agree with the producer's `servicing_case` summary for every case.
- Every column that the report reads from a Models and evaluation table exists in `data/output/lakehouse/gold/`. The account view shows a score, a score status and a segment for every scored account.

These tests skip when the files are missing. Run `cd data; python -m hbdata all` first. Set `HOMEBRIDGE_DATA_DIR` to use another `data/` folder.

To load the model with the Microsoft TOM library (needs the .NET SDK):

```powershell
dotnet run --project reports\tools\tmdl-check -- reports\HomeBridgeMortgage.SemanticModel\definition
```

## Deploy when the Fabric workspace is ready

1. Make sure the Gold tables in `logic/gold_schema.sql` exist in the Lakehouse.
2. Replace the two placeholders in `HomeBridgeMortgage.SemanticModel/definition/expressions.tmdl` with the Lakehouse SQL analytics endpoint and its ID.
3. Sync the folder through Fabric Git integration, or open `HomeBridgeMortgage.pbip` in Power BI Desktop and publish.
4. Apply the sensitivity label. Assign the two roles.
5. Measure the time from event to visible report change. The 60-second view target is not a Direct Lake guarantee.

## Open items

- `review_cases` needs a copy of the SharePoint list in the Lakehouse. The copy job is not designed yet.
- `report_calendar` needs one row per date. This package owns it. The job that fills it is not designed yet.
- The real score-age limit, and team confirmation of the candidate threshold 0.5593.

## Rules

- Show the score age. Mark a score as stale when the last scoring run failed (INV-5, T05).
- A missing payment feed shows as stale, not as zero arrears (T03).
- Show only routing information about support cases to authorised advisers.
- Apply a Purview sensitivity label to the semantic model and the report.
- The 60-second view target is not a Direct Lake guarantee. Measure it.
- Use synthetic data only. Never put connection secrets in the project files.
