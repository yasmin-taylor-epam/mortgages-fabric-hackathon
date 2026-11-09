# Deploy HomeBridge to Microsoft Fabric as code

**Status:** Written and tested offline. On 5 November 2026 the `workspace`, `core`, `upload`, `notebooks`, `pipeline` and `model` steps ran in a real workspace and finished. On 9 November 2026 the `model` step ran again in full, and all four report pages exported without an error. The opt-in steps are not confirmed in Fabric yet. Items are labelled **untested** until someone runs them in a real workspace.

This page answers one question: can the team deploy the build with config-as-code, or must it click through Fabric? The answer is **mostly code**. Five steps stay manual. Each one has a reason and a Microsoft source in [Item by item](#item-by-item).

All data is synthetic. HomeBridge is a fictional lender.

## Contents

1. [What the script does](#what-the-script-does)
2. [Steps for a team member](#steps-for-a-team-member)
3. [Item by item: automated, partly, or manual](#item-by-item)
4. [Manual steps that remain](#manual-steps-that-remain)
5. [Permissions to request](#permissions-to-request)
6. [Layout and the team Git repository](#layout-and-the-team-git-repository)
7. [How the notebooks work in Fabric and locally](#how-the-notebooks-work-in-fabric-and-locally)
8. [What was verified, and what was not](#what-was-verified-and-what-was-not)
9. [Fabric CLI equivalents](#fabric-cli-equivalents)
10. [Sources](#sources)

## What the script does

`scripts/deploy_fabric.ps1` (wrapper) runs `scripts/fabric/deploy_fabric.py`. You sign in once, in a browser. The script keeps the sign-in in an encrypted cache in your user profile (outside the repository), so the next run does not open the browser until the sign-in expires. Use `--no-token-cache` to turn that off. The script holds no password, token or tenant ID.

| Step | What happens | Tool |
| --- | --- | --- |
| `capacity` | **Opt-in.** Creates a Fabric capacity in Azure. | Azure Resource Manager |
| `workspace` | Finds the workspace by name or ID. Creates it when missing. Assigns the capacity. | Fabric REST API |
| `roles` | **Opt-in.** Assigns workspace roles from the settings. | Fabric REST API |
| `core` | Publishes the Lakehouse, Environment, Eventhouse, KQL database, 7 notebooks, the scoring pipeline and the Eventstream. | fabric-cicd |
| `streaming` | **Opt-in, `--only` only.** Turns streaming ingestion off for `RawEventsLanding`. The `core` step does this by itself when it is needed. | Kusto management endpoint |
| `retention` | **Opt-in.** Sets the 7-day soft-delete retention of `RawEventsLanding`. | Kusto management endpoint |
| `upload` | Generates the synthetic exports. Uploads them and the Python code (`hbdata`, `models_eval`) to the Lakehouse Files area. | OneLake (ADLS Gen2) API |
| `notebooks` | Runs notebooks 00 to 05 in order, then 06 (Parquet to Delta tables). | Fabric Job Scheduler API |
| `pipeline` | Runs `pl_scheduled_scoring` once, then notebook 06 again. | Fabric Job Scheduler API |
| `schedule` | **Opt-in.** Schedules `pl_scheduled_scoring` (default every 15 minutes). | Fabric Job Scheduler API |
| `model` | Refreshes the Lakehouse SQL analytics endpoint, publishes the semantic model and the report, then refreshes the model. Fills the Direct Lake placeholders with the real SQL endpoint. | Fabric REST API, fabric-cicd |
| `rls` | **Opt-in.** Adds group members to the two semantic model roles. | XMLA endpoint (PowerShell and TOM) |
| `labels` | Sets a Purview sensitivity label on the model and report. Needs a Fabric administrator. Off unless `sensitivity_label_id` is set. | Fabric Admin REST API |
| `sharepoint` | **Opt-in.** Creates the review-case lists. | Microsoft Graph |
| `eventstream` | **Opt-in, `--only` only.** Reads the custom endpoint details of the Eventstream and prints names, never a key. | Fabric REST API |
| `replay` | **Opt-in.** Sends the replay events to the Eventstream with the connection read at run time. | Fabric REST API, `hbdata replay` |
| `git` | **Opt-in.** Connects the workspace to a GitHub branch. | Fabric REST API |

An **opt-in** step does nothing unless its section in the settings file has `enabled: true`, or you name the step with `--only`. A default run is the same as before. Every opt-in step is **untested** in Fabric.

Three modes:

- `--validate` runs offline checks. No sign-in. It builds the staging folder, checks every item file, checks the settings of every enabled section, and converts the SharePoint schema to Graph request bodies.
- `--dry-run` signs in, reads the tenant, and prints what it would do. It changes nothing. The Kusto `.show` command is a read. The retention `.alter-merge` command is never sent in a dry run.
- No flag applies the changes.

Every opt-in step is **idempotent**. It reads the current state first and changes only what differs.

Settings come from `fabric/config/fabric.config.yml` (workspace name or ID, capacity, run times, opt-in sections), then from `HB_FABRIC_*` environment variables, then from command-line options. The file holds no secrets. The script refuses a file that has keys such as `password`, `token`, `pat`, `connection_string`, `email` or `tenant_id`. It also refuses a value that looks like a GitHub PAT, a connection string or an e-mail address.

Secrets come from environment variables at run time. The script reads them and passes them on in memory. It never writes them to a file or prints them.

| Variable | Used by | Holds |
| --- | --- | --- |
| `HB_FABRIC_CAPACITY_ADMINS` | `capacity` | Comma-separated capacity admins (user principal names or app IDs). |
| `HB_GITHUB_PAT` | `git` | A GitHub personal access token. Not needed when `git.connection_id` is set or a connection with that name exists. |
| `HB_GRAPH_CLIENT_ID`, `HB_GRAPH_CLIENT_SECRET`, `HB_GRAPH_TENANT_ID` | `sharepoint` | An app registration for app-only Graph access. Without all three, the step uses your own sign-in. |

### Order, and why

The order follows two rules.

1. fabric-cicd publishes item types in a fixed order: Lakehouse, Environment, Eventhouse, Notebook, Semantic model, Report, KQL database, Pipeline, Eventstream. A file can point to another item only if that item is published earlier. The offline check tests this.
2. The semantic model uses Direct Lake. It needs the Delta tables to exist and the Lakehouse SQL endpoint to be ready. So the model is published in a second pass, after the notebooks have run.

## Steps for a team member

Run these from the repository root, in PowerShell. You need Python 3.10 or later.

```powershell
# 1. Offline check. No sign-in. Takes seconds.
powershell -File scripts\deploy_fabric.ps1 --validate

# 2. Copy the settings file and edit it: set workspace_name and capacity_name.
Copy-Item fabric\config\fabric.config.example.yml fabric\config\fabric.config.yml
notepad fabric\config\fabric.config.yml

# 3. Dry run. Opens one browser sign-in. Reads only.
powershell -File scripts\deploy_fabric.ps1 --dry-run

# 4. Deploy and run everything. Same sign-in.
powershell -File scripts\deploy_fabric.ps1
```

Useful variants:

```powershell
# Use an existing workspace and skip the capacity step
powershell -File scripts\deploy_fabric.ps1 --workspace-id <workspace-guid>

# Pass the settings on the command line instead of the file
powershell -File scripts\deploy_fabric.ps1 --workspace-name HomeBridge-Dev --capacity-name <capacity display name>

# Only publish the items. Do not upload or run anything.
powershell -File scripts\deploy_fabric.ps1 --only workspace core

# Run the notebooks and pipeline again
powershell -File scripts\deploy_fabric.ps1 --only notebooks pipeline

# Re-use an existing az login instead of the browser sign-in
powershell -File scripts\deploy_fabric.ps1 --auth azcli

# Opt-in steps. Set the section in the settings file first (see fabric.config.example.yml), or just name the step.
powershell -File scripts\deploy_fabric.ps1 --dry-run --only roles retention schedule
powershell -File scripts\deploy_fabric.ps1 --only retention schedule
$env:HB_GITHUB_PAT = '<paste for this session only>'; powershell -File scripts\deploy_fabric.ps1 --only git
powershell -File scripts\deploy_fabric.ps1 --only eventstream replay    # needs data\output and: pip install azure-eventhub
powershell -File scripts\deploy_fabric.ps1 --only model rls             # run rls again after every model deploy
```

Notes:

- The first run takes a long time. The Environment publish and the model training are the slow parts.
- Re-running is safe for publishing and uploading. Re-running `notebooks` trains the models again and registers new model versions.
- The script never deletes items unless `prune: true` is set. Even then, fabric-cicd keeps Lakehouses and other data items unless a feature flag allows deletion.
- A model deploy can reset the role members. Run `rls` again after each `model` step.
- After the run, do the [manual steps](#manual-steps-that-remain).

## Item by item

Checked against Microsoft pages on 4 November 2026. **Every step that is marked automated is untested until someone has run it in Fabric.** Nobody has.

Legend:

- **Automated**: the script does it from files or settings in this repository.
- **Partly**: the script does one part. A person does the rest.
- **Manual**: no supported API exists, or the API needs a person who signs in interactively.
- **Opt-in**: the step is off. Switch it on in the settings file (`enabled: true`) or name it with `--only`.
- **UNVERIFIED**: the sources do not settle the point. The code or text says what it assumes.

| Item | Status | What the script does | Why the rest is manual, or the limit | Source |
| --- | --- | --- | --- | --- |
| Workspace | **Automated** | Creates it by name. Assigns the capacity. | The caller needs the right to create workspaces. | [Create Workspace](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/create-workspace), [Assign To Capacity](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/assign-to-capacity) |
| Capacity (paid F SKU) | **Automated**, opt-in step `capacity` | Creates `Microsoft.Fabric/capacities` in Azure. Skips it when it exists. Capacity admins come from `HB_FABRIC_CAPACITY_ADMINS` at run time. | The caller needs Contributor on an Azure resource group. Azure holds the subscription and quota. | [Capacities - Create Or Update (ARM, 2023-11-01)](https://learn.microsoft.com/en-us/rest/api/microsoftfabric/fabric-capacities/create-or-update) |
| Capacity (free trial) | **Manual** | Nothing. | A trial is started in the portal. I found no API for it in the pages I read. I did not confirm with Microsoft that none exists. | Not found. **UNVERIFIED.** |
| Workspace roles | **Automated**, opt-in step `roles` | Adds or updates role assignments from the settings (Entra object IDs). Keeps the ones that match. | The tenant setting that lets service principals call Fabric APIs is on only after a Fabric administrator turns it on. That matters only if CI runs the script. | [Add Workspace Role Assignment](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/add-workspace-role-assignment) (user and service principal: yes) |
| Lakehouse | **Automated** | Creates it. Fills it with data (step `upload`). | Git and fabric-cicd carry the metadata only. Tables and files are never in Git. | [Lakehouse Git](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-git-deployment-pipelines) |
| Notebooks (7) | **Automated** | Publishes them with the default Lakehouse and Environment bound by logical ID. Runs them in order. | The notebook Resources folder is not supported by the public API. The code is not stored there. | [Notebook definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/notebook-definition) |
| Environment and Python packages | **Automated** for public libraries. **Partly** for project code. | `shap`, `rfc3339-validator` and `jsonschema` install through `environment.yml`. The script uploads `hbdata` and `models_eval` to Lakehouse Files. | Whether fabric-cicd uploads custom wheels is **UNVERIFIED**. The build does not depend on it. | [Environment Git](https://learn.microsoft.com/en-us/fabric/data-engineering/environment-git-and-deployment-pipeline) |
| Pipeline `pl_scheduled_scoring`: publish and run once | **Automated** | Publishes it. Runs it once. | None. | [Run On Demand Item Job](https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job) |
| Pipeline schedule (every 15 minutes) | **Automated**, opt-in step `schedule` | Creates a Cron schedule (`interval` in minutes, 1 to 5,270,400). Changes the interval when it differs. The interval is a setting. | 15 minutes is a proposal to test. Microsoft does not promise that a run finishes inside the interval. | [Create Item Schedule](https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/create-item-schedule) (user and service principal: yes), [pipeline REST capabilities](https://learn.microsoft.com/en-us/fabric/data-factory/pipeline-rest-api-capabilities) |
| One as-of date (`run.as_of`) | **Automated**, opt-in (empty by default: real clock) | With `run.as_of` set (for the demo, `2026-10-16T09:00:00Z`), the deploy passes it to the pipeline run (`as_of`, which replaces `pipeline_cutoff`), writes it as the default of the pipeline parameter `as_of` (so the schedule uses it), and passes it to notebook 06, which writes the one-row table `report_settings`. The report measure `Report As Of` reads that table in place of `UTCNOW()`. The KQL function `Customer360(as_of)` takes the same value. The pipeline sets the run start to one minute after the cutoff. | Without it, the pipeline cutoff, the report measures and `Customer360()` use the real clock. The synthetic data stops on 15 Oct 2026. A real cutoff of 9 Nov 2026 scored 250 accounts, not 303. The local replay and scoring commands read `HB_AS_OF` the same way. Feed freshness is still checked at load time, not at the as-of time. The regex that sets the pipeline default passed the fabric-cicd validator offline. It has not run in Fabric. | [fabric-cicd parameterisation](https://microsoft.github.io/fabric-cicd/latest/how_to/parameterization/), [Run On Demand Item Job](https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job) |
| Pipeline retry | **Automated** (placeholder value) | The snapshot and scoring notebook activities retry once. The publish activity keeps 3 retries. | The retry count is a placeholder. The team has not confirmed it. A retried scoring run is a new run. | [Pipeline activity policy](https://learn.microsoft.com/en-us/fabric/data-factory/activity-overview) |
| Adviser outcomes table `review_outcomes` | **Automated** (empty table) | Notebook 06 creates the empty Delta table. The semantic model has the read-only measure `Alert Precision (Adviser Outcomes)`. See `docs/alert-threshold.md`. | Nothing writes to the table yet. "Agreed" is an adviser opinion, not a confirmed refinancing. Not run in Fabric. | [Direct Lake overview](https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-overview) |
| Pipelines `pl_load_raw`, `pl_copy_context`, `pl_export_journeys` | **Manual** (not built) | Notebook `00_load_raw` replaces `pl_load_raw`. | The other two need Copy activities with a KQL database connection. Nobody has verified a hand-written definition. Build them once in the portal, sync to Git, commit the files. | Not applicable |
| Eventstream: publish | **Automated** | Publishes a custom-endpoint source that routes to the table `RawEventsLanding` with **direct (queued) ingestion** (`dataIngestionMode: DirectIngestion`, mapping `RawEventsLanding_json`, `compatibilityLevel` stays 1.0). The destination node is named `RawEventsLandingDirect`. | The earlier definition used `ProcessedIngestion`. That mode needs streaming ingestion, and the deploy turns streaming off on purpose (see the KQL database row). The live destination showed "Failed". Direct mode reads the table and mapping that `DatabaseSchema.kql` creates, and its `itemId` is the **KQL database**. In a live test, Fabric refused the Eventhouse item there (`UnsupportedArtifactType`: "is not a database artifact. It is of type 'KustoEventHouse'"), although the Microsoft example page uses the Eventhouse item. The live item also kept `compatibilityLevel` 1.0. Fabric refused to change the type of the existing node (`Node type cannot be updated for node RawEventsLanding. Original type: KustoPushMode, attempted new type: KustoPullMode`). So the new node has a new name, and an update of an existing workspace swaps the node. The `connectionName` (`hb-raw-events-direct`) must be unique, up to 40 characters. Whether it is unique enough across workspaces is **UNVERIFIED**. The direct destination has not run in Fabric. | [Eventstream definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/eventstream-definition), [Eventstream with an Eventhouse DirectIngestion destination by API](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/api-kusto-pull-destination) |
| Eventstream: connection details for replay | **Automated**, opt-in steps `eventstream` and `replay` | Reads the custom endpoint connection with the Fabric API. Runs `python -m hbdata replay --target eventstream` with the connection in the child process environment only. The key is never printed or written. | The API supports custom endpoint, Cribl and SAP Datasphere sources only. Replay needs the local data in `data/output` and `pip install azure-eventhub`. The replay sends the events in as many batches as the Event Hub size limit needs, in order, each once. An event that is too big for one batch stops the run with a clear error. Tested offline with a fake producer. The real batch limit is **UNVERIFIED**. | [Get Eventstream Source Connection](https://learn.microsoft.com/en-us/rest/api/fabric/eventstream/topology/get-eventstream-source-connection) (user and service principal: yes), [Get Eventstream Topology](https://learn.microsoft.com/en-us/rest/api/fabric/eventstream/topology/get-eventstream-topology) |
| Eventhouse | **Automated** | Creates it. | None. | [Real-Time Intelligence Git](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/git-deployment-pipelines) |
| KQL database: tables, functions, update policies, mapping | **Automated** | Deploys `DatabaseSchema.kql`, generated from `kql/*.kql`. When the database does not exist, it first publishes it without the two `.alter table ... policy update` commands, turns streaming ingestion off for `RawEventsLanding`, then publishes everything. | A KQL database Git item holds a fixed list of command types. The streaming ingestion policy is not on the list. Fabric runs the script as one unit and removes the database when a command fails. The update policies read `AccountContext`, and Fabric refuses that while streaming ingestion is on for the source table. This ran once in a real workspace on 5 November 2026. With streaming off, events reach `RawEventsLanding` on the queued path, which batches them. The Eventstream destination uses direct (queued) ingestion for the same reason. Whether queued ingestion fits the 60-second target is **UNVERIFIED**. | [KQL database Git](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/git-eventhouse-kql-database), [Streaming ingestion policy](https://learn.microsoft.com/en-us/kusto/management/streaming-ingestion-policy?view=microsoft-fabric) |
| KQL table retention (7 days on `RawEventsLanding`) | **Automated**, opt-in step `retention` | Reads the policy with `.show table ... policy retention`. Runs `.alter-merge table ... policy retention softdelete = 7d` only when the value differs. Uses the Kusto management endpoint `/v1/rest/mgmt` on the KQL database query URI. | `DatabaseSchema.kql` cannot hold this command. `DatabaseProperties.json` holds only `oneLakeCachingPeriod` and `oneLakeStandardStoragePeriod`, so it cannot hold a table retention either. The Kusto token audience `https://api.kusto.windows.net/.default` was accepted by a Fabric eventhouse on 5 November 2026 (a `.show version` call and a `.show tables` call worked). The Kusto database name is the KQL database item ID, not its display name. The `.alter-merge` retention command itself has not run. | [Retention policy command](https://learn.microsoft.com/en-us/kusto/management/retention-policy?view=microsoft-fabric), [Kusto REST request](https://learn.microsoft.com/en-us/kusto/api/rest/request?view=microsoft-fabric), [KQL database definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/kql-database-definition) |
| Activator rules | **Manual** | Nothing. | The definition schema is now published. It lists sources (simulator, KQL, Real-time Hub, Eventstream), a Fabric item action and time series views for events, objects, attributes and rules. The entity list has no Power Automate custom action. The two designed rules start a Power Automate flow, so I cannot express them in a definition that validates against the published schema. A rule that runs a Fabric item (for example a pipeline) would validate. That changes the design, so I did not build it. I read the entity list and the source and action sections. I did not read every rule payload section. | [Reflex definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/reflex-definition) |
| Semantic model and report: publish | **Automated** | Refreshes the SQL analytics endpoint first. Then publishes both. Fills the SQL endpoint and endpoint ID in `expressions.tmdl`. | Git preview items. The second Direct Lake placeholder is named `LAKEHOUSE-ID`. Direct Lake on SQL expects the SQL endpoint ID, and the script uses that. This worked on 6 November 2026: the deployed model held the SQL endpoint ID and all 14 tables returned rows. The first deploy had no endpoint refresh, and the report failed with "Invalid object name 'dbo.mortgage'" until the endpoint was refreshed by hand. A published Direct Lake model is not framed until it is refreshed. Until then queries fall back to DirectQuery, and the large ones failed with "Query has exceeded the available resources". With fallback switched off, Fabric reported "The dataset has not been refreshed". The step now refreshes the model after the publish. Both refreshes ran inside a deploy on 9 November 2026. The measure `Days At Stage` also used too much memory on an F2 capacity. It now uses iterators and gives the same values on 25 accounts. | [Semantic model Git](https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration) |
| Semantic model role members (RLS) | **Partly**, opt-in step `rls` | Runs `scripts/fabric/set_rls_members.ps1`. The script connects to the XMLA endpoint with TOM and adds group object IDs to the two roles. It only adds. It skips members that exist. The token goes to the child process environment only. | The REST API cannot set role members. TMDL, Git and fabric-cicd deploy roles with empty members, so run the step again after each model deploy. The capacity needs XMLA read-write. Whether TOM accepts a group by object ID is **UNVERIFIED**. Reports say a group that is not mail-enabled can fail with "invalid rolememberships". If it fails, add the members in the portal. The script could not be run here. Only its syntax and its call from Python are tested. | [XMLA endpoint](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-connect-tools), [RLS role membership](https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security) |
| MLflow experiment and models | **Automated** by running notebook 03 | Notebook 03 creates the experiment `homebridge-refinancing-risk` and registers `hb-refi-clf` and `hb-segments`. | fabric-cicd and Git track only experiment metadata. Runs and model versions are never in Git. | [ML experiments Git](https://learn.microsoft.com/en-us/fabric/data-science/machine-learning-artifacts-git-deployment-pipelines) |
| SharePoint review-case lists | **Partly**, opt-in step `sharepoint` | Reads `action/sharepoint/review-cases.list.json`. Creates each missing list with its columns through Microsoft Graph. Adds missing columns to a list that exists. Signs in as you, or app-only when `HB_GRAPH_CLIENT_ID`, `HB_GRAPH_CLIENT_SECRET` and `HB_GRAPH_TENANT_ID` are set. | The site must exist. Creating a site needs the SharePoint site-creation REST API (SPSiteManager). I did not build or verify that. Granting users and groups access needs PnP or SharePoint REST. The step does not set permissions. A site owner must secure the lists. Whether Graph keeps `enforceUniqueValues` on `case_key` (INV-3) is **UNVERIFIED**. Check it after the first run. `action/sharepoint/provision-list.ps1` (PnP) stays as the alternative. | [Create list (Graph)](https://learn.microsoft.com/en-us/graph/api/list-create?view=graph-rest-1.0): application permission `Sites.Manage.All` or `Sites.ReadWrite.All` |
| Power Automate flow | **Partly** | Nothing in this script. | A solution can be exported and imported with the Power Platform CLI (`pac solution`). I did not verify that here. The first creation of a connection (SharePoint, Outlook, Teams) needs a person to sign in. The flow design is in `action/power_automate/review-case-writer.md`. | [Power Platform CLI solution](https://learn.microsoft.com/en-us/power-platform/developer/cli/reference/solution). **UNVERIFIED** here. |
| Link from Activator to the flow | **Manual** | Nothing. | Microsoft documents this as a portal feature. The Reflex schema has no entity for it. | [Activator custom actions with Power Automate](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-trigger-power-automate-flows) |
| Purview sensitivity labels | **Partly** | The `labels` step sets a label on the model and report when `sensitivity_label_id` is set. | The API needs a Fabric administrator who signs in as a user. It does not support a service principal. It allows 25 requests an hour. The label must be in that administrator's label policy. A Purview default-label policy may cover new items. I did not verify that. | [Bulk Set Labels](https://learn.microsoft.com/en-us/rest/api/fabric/admin/labels/bulk-set-labels) |
| Data upload to OneLake | **Automated** | Sends exports and code to `Files/hb/`. | None. | [OneLake and Python](https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-python) |
| Run notebooks and the pipeline | **Automated** with a user sign-in | Job Scheduler API. | A service principal can call the job API. Some notebook features have limits under a service principal (for example semantic link functions, and `notebookutils.runtime.context` in fabric-cicd issue 202). I did not verify either. | [Run On Demand Item Job](https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job) |
| Git connection to GitHub | **Automated**, opt-in step `git` | Creates a Fabric connection from a PAT in `HB_GITHUB_PAT`, or uses an existing one. Connects. Initialises. Runs Update From Git when asked. Does nothing when the workspace is already connected. | A GitHub connection needs a PAT. Fabric has no GitHub service principal credential. Connect needs a workspace admin. A service principal can connect only with a configured connection. The step never commits workspace changes to the branch. The repository keeps items in `fabric/items/` and `reports/`. Fabric Git reads one folder, so set `git.directory` to a folder that holds all items, or move them first. | [Git - Connect](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/connect), [Initialize Connection](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/initialize-connection), [Update From Git](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/update-from-git), [Automate Git integration](https://learn.microsoft.com/en-us/fabric/cicd/git-integration/git-automation), [Create Connection](https://learn.microsoft.com/en-us/rest/api/fabric/core/connections/create-connection) |
| Tenant setting: service principals can call Fabric APIs | **Manual**, once | Nothing. | Only a Fabric administrator can turn it on. | [Developer settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer) |

### One as-of date

The data is frozen on 15 October 2026. Set `run.as_of` in the settings file (`2026-10-16T09:00:00Z` for the demo) so that the pipeline, the report and the KQL view all use it. Leave it empty to use the real clock. To run the Python tools locally with the same date, set the environment variable `HB_AS_OF`. Not run in Fabric.

## Manual steps that remain

This is the short list of what stays manual. Each has a reason.

| # | Step | Why it stays manual |
| --- | --- | --- |
| 1 | **Start a Fabric trial capacity** (only if you use a trial) | I found no API for a trial. A paid F SKU can be created by the `capacity` step. |
| 2 | **Set the Purview sensitivity label** (`bulkSetLabels`) | The API needs an interactive Fabric administrator. It rejects a service principal. |
| 3 | **Authorise connections for Power Automate the first time** | A person must sign in to SharePoint, Outlook or Teams once. |
| 4 | **Link the Activator rules to the Power Automate flow** | The link is a portal action. The published Reflex schema has no entity for it. The rules themselves cannot be expressed in a definition either. See the Activator row above. |
| 5 | **Turn on the tenant setting "Service principals can call Fabric public APIs"** (CI only) | Only a Fabric administrator can change it. A person who runs the script with their own sign-in does not need it. |

Also manual, and outside this list because they are not about Fabric itself:

- **Build the Activator rules and the Power Automate flow** from `action/activator/rules.md` and `action/power_automate/review-case-writer.md`. Build the two rules once, sync the item to Git and commit it. After that, the deploy can publish the Activator item. (The research notes say fabric-cicd lists Reflex as a supported type. I did not re-check that.)
- **Create the SharePoint site and secure the lists.** The `sharepoint` step creates the lists only.
- **Build `pl_copy_context` and `pl_export_journeys`** in the portal and commit the export.
- **Check the opt-in steps by hand once.** They are untested. If `rls` fails, add the group members in the portal (semantic model, Security).
- **Measure the 60-second view target.** It is not a Direct Lake guarantee.

If the model fails on first load, wait a few minutes and refresh. The SQL analytics endpoint can lag behind new Delta tables. This happened on 6 November 2026. The `model` step now refreshes the endpoint before the publish and the model after it. To do it alone, run `--only workspace model`.

## Permissions to request

Ask for these so that the manual list shrinks. The first group is enough for the steps in this page. The second group is only for CI/CD with a service principal.

### For a person running the script

| Permission | Who grants it | What it unlocks | Source |
| --- | --- | --- | --- |
| Fabric enabled for you, and the right to create workspaces | Fabric administrator | `workspace` step | REST: Create Workspace |
| Contributor or admin on the Fabric capacity | Capacity admin | Create and assign a workspace on that capacity | REST: Create Workspace, Assign To Capacity |
| Workspace role Admin | Workspace admin | Assign the capacity. Publish items. Run jobs. | REST: Assign To Capacity |
| Real-Time Intelligence, data science and Activator items not blocked in the tenant | Fabric administrator | Eventhouse, KQL database, Eventstream, ML items | **Unverified.** Check in the tenant settings. |
| Fabric administrator role (one person) | Tenant admin | `labels` step. Also needed to read the label list. | REST: Bulk Set Labels |
| A Purview sensitivity label in that person's label policy | Compliance admin | Which label can be applied | REST: Bulk Set Labels |
| Workspace role Member or higher | Workspace admin | `roles` step (adds members and lower roles only) | REST: Add Workspace Role Assignment |
| Workspace role Admin | Workspace admin | `git` step (Connect and Initialize need Admin) | REST: Git - Connect |
| Contributor on an Azure resource group | Azure owner | `capacity` step | ARM: Capacities - Create Or Update |
| Read and write permission on the Eventstream | Workspace admin | `eventstream` and `replay` steps | REST: Get Eventstream Source Connection |
| XMLA endpoint set to read-write on the capacity, and Build permission on the model | Capacity admin | `rls` step | XMLA endpoint page |
| Application permission `Sites.Manage.All` or `Sites.ReadWrite.All` (app-only), or the matching delegated right | Entra admin | `sharepoint` step | Graph: Create list |

### For a service principal (CI/CD later)

| Permission | What it unlocks | Source |
| --- | --- | --- |
| Tenant setting **Service principals can call Fabric public APIs** | Any Fabric REST call from a service principal or managed identity | REST: identity support, Developer settings |
| Tenant setting **Service principals can create workspaces, connections, and deployment pipelines** | Create a workspace or a connection in code (`workspace` and `git` steps) | REST: Create Workspace, Create Connection |
| Capacity contributor for the service principal | Assign a workspace to a capacity | REST: Assign To Capacity |
| A workspace role for the service principal | Publish items and run jobs | Fabric docs |

Limits to know:

- The bulk-set-labels API does not support a service principal. Labels need a human Fabric administrator, or a manual step.
- Git Connect works for a service principal only with a configured connection (`myGitCredentials.source` = `ConfiguredConnection`). A GitHub connection needs a PAT.
- Each API page says whether it supports a service principal. An item type or job type can refuse one. Check before you move a step to CI.
- The `rls` step runs with the signed-in identity. A service principal on the XMLA endpoint is **UNVERIFIED** here.

### For the items outside Fabric

- **SharePoint:** site owner rights, and either your own sign-in with the Graph rights above or an Entra app registration (the three `HB_GRAPH_*` variables). The PnP script needs an app registration too.
- **Power Automate:** a licence, and the right to use the connectors that the flow needs. The README of `action/` says these are not confirmed.
- **Azure:** Contributor on a resource group, to create a Fabric capacity.

## Layout and the team Git repository

The team repository `EPAM-Fabric-Hackhathon/azure-mafia` is not accessible from here. This build stays compatible with Fabric Git integration. Nobody has tested the move.

```text
fabric/
  items/                                   Git-format items. Copy this folder's content into a workspace folder.
    HomeBridgeLakehouse.Lakehouse/
    HomeBridgeEnvironment.Environment/
    HomeBridgeEventhouse.Eventhouse/
      .children/HomeBridgeEvents.KQLDatabase/
    HomeBridgeRiskEvents.Eventstream/
    pl_scheduled_scoring.DataPipeline/
    00_load_raw.Notebook/ ... 06_publish_delta_tables.Notebook/   generated from notebooks/*.ipynb
  config/fabric.config.example.yml         settings for the deploy script
reports/
  HomeBridgeMortgage.SemanticModel/        already in Git format
  HomeBridgeMortgage.Report/               already in Git format
scripts/fabric/                            deploy tooling and its tests
```

To move the items into the team repository:

1. Copy the folders under `fabric/items/` and the two folders under `reports/` into the workspace root folder of the team repository.
2. Keep the `.platform` files. Their `logicalId` values tie the items together. They are the same in every workspace.
3. Fabric Git integration does not carry data. Run the `upload` and `notebooks` steps in each workspace, so that the Python code and the tables exist.
4. The `.ipynb` files in `notebooks/` stay the source of the notebooks. Fabric Git integration writes `notebook-content.py` back when someone edits a notebook in Fabric. Port such a change to the `.ipynb` file, then run `python scripts/fabric/build_fabric_items.py`. A test fails when the two differ.

Two files are generated. Do not edit them by hand:

- `fabric/items/*.Notebook/*` from `notebooks/*.ipynb`.
- `DatabaseSchema.kql` from `kql/*.kql`. The script strips the `.execute database script` wrapper and leaves out the one unsupported command.

Regenerate with `python scripts/fabric/build_fabric_items.py`. Check without writing with `--check`.

## How the notebooks work in Fabric and locally

Each notebook checks whether `/lakehouse/default/Files` exists.

| | Local | Fabric |
| --- | --- | --- |
| Code | `data/hbdata`, `notebooks/models_eval` | `Files/hb/code/data/...`, `Files/hb/code/notebooks/...` (same layout as the repository) |
| Data | `data/output/` | `Files/hb/output/` |
| MLflow | SQLite file under the output folder | The workspace tracking server (`HB_MLFLOW_URI=fabric`) |
| Parameters | The `parameters` cell | The `parameters` cell. A pipeline or the Job API overrides it. |

The notebooks keep their local behaviour. The local test suites and `scripts/run_local_flow.ps1` still pass.

New notebooks:

- `00_load_raw` loads the exports into append-only raw tables. It is a stand-in for the `pl_load_raw` pipeline.
- `06_publish_delta_tables` copies the Parquet tables that the semantic model reads into Delta tables. It also builds `report_calendar` and creates an empty `review_cases` when it does not exist. The Dashboard and action package owns those two tables. The real jobs for them are not designed yet. Notebook 06 runs only in Fabric.

Not built in Fabric: `pl_copy_context` and `pl_export_journeys`. Without the staged journey results, live snapshots use no journey events. The scheduled run still works.

`risk_updated` events go to a JSON-lines file in Lakehouse Files. The Eventstream connection is not wired.

## What was verified, and what was not

Verified offline (see `scripts/fabric/tests/`):

- Every item folder has a valid `.platform` file. The folder name matches the display name. Logical IDs are unique.
- Every reference between items (notebook to Lakehouse and Environment, pipeline to notebook, Eventstream to KQL database, KQL database to Eventhouse) points to an item of the right type. The target is published earlier than the item that refers to it. The order is checked against fabric-cicd v1.4.0.
- Notebook files have valid `# META` JSON, one parameters cell, and the Lakehouse binding.
- `DatabaseSchema.kql` holds only command types that Fabric lists as supported.
- The fabric-cicd config validator accepts the generated configs. The parameter file filters match the staged items.
- The deploy steps run in the right order against a fake Fabric API. A dry run sends no change request. A failed job stops the run.
- Notebook 00 runs locally and is idempotent. The set of Delta tables in notebook 06 equals the set of tables in the semantic model. The source Parquet files exist.
- No secret patterns appear in the item files. The config loader rejects secret keys and values that look like a PAT, a connection string or an e-mail address.
- Each opt-in step (`capacity`, `roles`, `retention`, `schedule`, `rls`, `sharepoint`, `eventstream`, `replay`, `git`) runs against a fake HTTP session. The tests check the request bodies, the idempotence (a second run sends no change), the dry run (no change request, no retention `.alter-merge`), and that no key, PAT or token appears in the log or in a file. `scripts/fabric/set_rls_members.ps1` passes a PowerShell syntax check.

**Not verified. Treat all of this as untested in Fabric:**

- Any call to a real Fabric, OneLake, Azure Resource Manager, Kusto, Graph, SharePoint or Entra endpoint. This includes every opt-in step.
- That Fabric accepts the notebook, pipeline, Eventstream, Eventhouse and Environment files as written.
- The Spark behaviour of notebook 06. The Python 3.11 compatibility of the packages (they were tested on 3.13).
- The shape of the job parameters for notebooks and pipelines. It follows the Fabric CLI source and the pipeline REST docs.
- That the Eventstream direct ingestion (node `RawEventsLandingDirect`) maps the JSON envelope to `RawEventsLanding`, that Fabric accepts the `connectionName`, and that the node swap works on an existing Eventstream.
- The report in a browser. It was checked as an exported PDF, with no account selected. Page 1 is mostly empty until a person selects an account.
- The `model` step with both refreshes inside a full deploy from an empty workspace. It ran on its own on 9 November 2026.
- Service principal support for notebook jobs.
- Whether queued ingestion (streaming is off for `RawEventsLanding`) meets the 60-second target.
- The `.alter-merge` retention command, the schedule, roles, Git, Eventstream, replay, SharePoint, capacity and RLS steps in a real workspace.
- That TOM accepts a group by object ID (`rls`). The script has never run. XMLA read-write on a capacity is a precondition.
- The result URL that the code reads after a long-running Initialize Connection call (`git`).
- That Graph keeps `enforceUniqueValues` and the index on `case_key` (`sharepoint`).
- Whether a trial capacity has an API. SharePoint site creation (SPSiteManager), `pac solution` and Purview default-label policies were taken from research notes and not re-checked.
- Sensitivity labels, and anything in Power Automate or Activator.

## Fabric CLI equivalents

The script does not call the Fabric CLI (`fab`). Reason: `fab` keeps its own sign-in. Using it would mean a second sign-in. The CLI documents that `fab deploy` runs the same library, fabric-cicd. These commands are the manual equivalents. All are **untested** here.

```powershell
pip install ms-fabric-cli
fab auth login                                              # interactive browser sign-in
fab mkdir HomeBridge-Dev.Workspace -P capacityName=<capacity>
fab cp ./build/fabric/data/exports HomeBridge-Dev.Workspace/HomeBridgeLakehouse.Lakehouse/Files/hb/output/exports -r
fab job run HomeBridge-Dev.Workspace/00_load_raw.Notebook
fab job run HomeBridge-Dev.Workspace/pl_scheduled_scoring.DataPipeline -i '{"parameters":{"run_kind":"scheduled"}}'
fab deploy --config build/fabric/core.config.yml --target_env dev   # after a script run wrote that file
```

The `microsoft/skills-for-fabric` repository holds Copilot skills for Fabric work, for example `git-integration-operations-cli` and `eventstream-cli`. They guide an assistant. They are not a deploy engine. This build does not depend on them.

## Sources

Checked on 4 November 2026. The Learn pages are the authority. Where this page says **unverified**, no source in this list settles the point.

- fabric-cicd documentation and source, v1.4.0: <https://microsoft.github.io/fabric-cicd/latest/> (supported item types, parameterization, config deployment, publish order in `constants.py`, shell-only publish for Lakehouse and ML experiment, report path rewrite).
- Fabric CLI documentation, v1.9.0: <https://microsoft.github.io/fabric-cli/> (`mkdir`, `cp`, `job`, `deploy`, `label`).
- Git integration, supported items: <https://learn.microsoft.com/en-us/fabric/cicd/git-integration/intro-to-git-integration>
- Lakehouse Git: <https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-git-deployment-pipelines>
- Notebook Git and definition formats: <https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-source-control-deployment> and <https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/notebook-definition>
- Environment Git: <https://learn.microsoft.com/en-us/fabric/data-engineering/environment-git-and-deployment-pipeline>
- Real-Time Intelligence Git (Eventstream, Eventhouse, KQL database, Activator): <https://learn.microsoft.com/en-us/fabric/real-time-intelligence/git-deployment-pipelines> and the pages linked from it. Eventstream definition: <https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/eventstream-definition> and <https://github.com/microsoft/fabric-event-streams/blob/main/API%20Templates/eventstream-definition.json>
- Machine learning experiments and models, Git (preview): <https://learn.microsoft.com/en-us/fabric/data-science/machine-learning-artifacts-git-deployment-pipelines>
- REST: Create Workspace <https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/create-workspace>, Assign To Capacity <https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/assign-to-capacity>, Run On Demand Item Job <https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job>, Bulk Set Labels <https://learn.microsoft.com/en-us/rest/api/fabric/admin/labels/bulk-set-labels>, identity support <https://learn.microsoft.com/en-us/rest/api/fabric/articles/identity-support>
- Added for the opt-in steps (read on 4 November 2026): Create Item Schedule <https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/create-item-schedule>, Add Workspace Role Assignment <https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/add-workspace-role-assignment>, Git Connect, Initialize Connection and Update From Git <https://learn.microsoft.com/en-us/rest/api/fabric/core/git/connect>, Create Connection <https://learn.microsoft.com/en-us/rest/api/fabric/core/connections/create-connection>, Automate Git integration <https://learn.microsoft.com/en-us/fabric/cicd/git-integration/git-automation>, Get Eventstream Source Connection <https://learn.microsoft.com/en-us/rest/api/fabric/eventstream/topology/get-eventstream-source-connection>, KQL retention policy <https://learn.microsoft.com/en-us/kusto/management/retention-policy?view=microsoft-fabric>, Kusto REST request <https://learn.microsoft.com/en-us/kusto/api/rest/request?view=microsoft-fabric>, KQL database definition <https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/kql-database-definition>, Capacities Create Or Update (ARM) <https://learn.microsoft.com/en-us/rest/api/microsoftfabric/fabric-capacities/create-or-update>, XMLA endpoint <https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/service-premium-connect-tools>, RLS membership <https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security>, Graph Create list <https://learn.microsoft.com/en-us/graph/api/list-create?view=graph-rest-1.0>, Reflex definition <https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/reflex-definition>, Developer tenant settings <https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer>.
- Community report on RLS by group object ID (not a Microsoft source): <https://github.com/microsoft/semantic-link-labs/discussions/948>
- Fabric CLI source (job parameter shape): <https://github.com/microsoft/fabric-cli>
- Copilot skills for Fabric: <https://github.com/microsoft/skills-for-fabric>
