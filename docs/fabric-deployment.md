# Deploy HomeBridge to Microsoft Fabric as code

**Status:** Written and tested offline only. Nobody has run any of this in Fabric. Every claim that a step "works in Fabric" is a target, not a result. Items are labelled **untested** until someone runs them in a real workspace.

This page answers one question: can the team deploy the build with config-as-code, or must it click through Fabric? The answer is **mostly code**. A short list of steps stays manual. Each one has a reason and a way to shrink the list.

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

`scripts/deploy_fabric.ps1` (wrapper) runs `scripts/fabric/deploy_fabric.py`. You sign in once, in a browser. The script holds no password, token or tenant ID.

| Step | What happens | Tool |
| --- | --- | --- |
| `workspace` | Finds the workspace by name or ID. Creates it when missing. Assigns the capacity. | Fabric REST API |
| `core` | Publishes the Lakehouse, Environment, Eventhouse, KQL database, 7 notebooks, the scoring pipeline and the Eventstream. | fabric-cicd |
| `upload` | Generates the synthetic exports. Uploads them and the Python code (`hbdata`, `models_eval`) to the Lakehouse Files area. | OneLake (ADLS Gen2) API |
| `notebooks` | Runs notebooks 00 to 05 in order, then 06 (Parquet to Delta tables). | Fabric Job Scheduler API |
| `pipeline` | Runs `pl_scheduled_scoring` once, then notebook 06 again. | Fabric Job Scheduler API |
| `model` | Publishes the semantic model and the report. Fills the Direct Lake placeholders with the real SQL endpoint. | fabric-cicd |
| `labels` | Sets a Purview sensitivity label on the model and report. Needs a Fabric administrator. Off unless `sensitivity_label_id` is set. | Fabric Admin REST API |

Three modes:

- `--validate` runs offline checks. No sign-in. It builds the staging folder and checks every item file.
- `--dry-run` signs in, reads the tenant, and prints what it would do. It changes nothing.
- No flag applies the changes.

Settings come from `fabric/config/fabric.config.yml` (workspace name or ID, capacity, run times), then from `HB_FABRIC_*` environment variables, then from command-line options. The file holds no secrets. The script refuses a file that has keys such as `password`, `token` or `tenant_id`.

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
```

Notes:

- The first run takes a long time. The Environment publish and the model training are the slow parts.
- Re-running is safe for publishing and uploading. Re-running `notebooks` trains the models again and registers new model versions.
- The script never deletes items unless `prune: true` is set. Even then, fabric-cicd keeps Lakehouses and other data items unless a feature flag allows deletion.
- After the run, do the [manual steps](#manual-steps-that-remain).

## Item by item

Legend: **Full** = deployed by the script from files in this repository. **Partly** = some of it is automated. **Manual** = a person must do it.

| Item | Status | What is automated | Why the rest is manual, or the limit |
| --- | --- | --- | --- |
| Workspace | **Full** | Create by name. | The caller needs the "create workspaces" right from a Fabric admin. |
| Capacity | **Manual** (use an existing one) | The script assigns the workspace to a capacity that you name. | Creating a capacity is an Azure step. It needs rights on an Azure subscription. The Fabric CLI documents `fab mkdir <name>.Capacity` with SKU, admin, location, resource group and subscription. **Untested.** The script does not create capacities. |
| Lakehouse | **Full** | Create. | Git and fabric-cicd carry only the metadata. Tables and files are never in Git. The script fills the Lakehouse with data instead. |
| Notebooks (7) | **Full** | Publish with the default Lakehouse and Environment bound by logical ID. Run in order. | The notebook Resources folder is not supported by the public API. The code is not stored there. |
| Environment and Python packages | **Full** for public libraries. **Partly** for the project code. | `shap`, `rfc3339-validator` and `jsonschema` install through `environment.yml`. The script uploads `hbdata` and `models_eval` to Lakehouse Files. Notebooks add that folder to `sys.path`. | An Environment can also hold custom wheels. Whether fabric-cicd uploads them is **unverified**, so the build does not depend on it. The Environment publish is asynchronous. fabric-cicd waits for it. |
| Data pipeline `pl_scheduled_scoring` | **Full** | Publish. Run once. | The 15-minute schedule is not set. The Job Scheduler API can create schedules. This is not built, so the pipeline never runs by itself. |
| Data pipelines `pl_load_raw`, `pl_copy_context`, `pl_export_journeys` | **Manual** (not built) | Notebook `00_load_raw` replaces `pl_load_raw`. | The other two need Copy activities with a KQL database connection. Nobody has verified a hand-written definition. Build them once in the portal, sync to Git, and commit the exported files. |
| Eventstream | **Partly**. **Untested.** | Publish a custom-endpoint source that routes to `RawEventsLanding` in the KQL database. | The custom endpoint connection string appears only after creation. Nothing reads it yet, so `replay.send_eventstream` has no endpoint. A Direct Ingestion destination needs a manual connection after import. This build uses processed ingestion to avoid that. |
| Eventhouse | **Full** | Create. | None. |
| KQL database and KQL scripts | **Full**, one command manual | Tables, functions, update policies and the ingestion mapping come from `DatabaseSchema.kql`, generated from `kql/*.kql`. | A KQL database Git item holds only a fixed list of command types. The 7-day retention policy on `RawEventsLanding` is not in the list. Run it by hand. See [manual steps](#manual-steps-that-remain). |
| Activator rules | **Manual** | Nothing. | The Git file `ReflexEntities.json` has no published schema. Microsoft says the schemas are coming. A hand-written file would be a guess. fabric-cicd can publish an Activator that someone exported. Build the two rules once, sync to Git, commit the files. |
| Semantic model | **Full**. **Untested.** | Publish. Fill the SQL endpoint and endpoint ID in `expressions.tmdl`. | The model is a Git preview item. Members of the two roles are assigned in the portal. The README of `reports/` says the same. The second Direct Lake placeholder is named `LAKEHOUSE-ID`. Direct Lake on SQL expects the SQL endpoint ID, and the script uses that. **Unverified.** If the model fails to load, try the Lakehouse ID. |
| Report | **Full**. **Untested.** | Publish. fabric-cicd rewrites the path to the model into a link to the deployed model. | The report is a Git preview item. |
| MLflow experiment and models | **Full by running notebook 03** | Notebook 03 creates the experiment `homebridge-refinancing-risk` and registers `hb-refi-clf` and `hb-segments`. | fabric-cicd and Git track only experiment metadata. Runs and model versions are never in Git. fabric-cicd has no ML model item type. The models exist only after the training run. |
| SharePoint review-case lists | **Partly**. **Untested.** | `action/sharepoint/provision-list.ps1` creates the lists. | It is not part of Fabric. It needs a SharePoint site, an Entra app registration, the PnP.PowerShell module and a second sign-in. The script is not in the deploy run. A site owner must secure the lists. |
| Power Automate flow | **Manual** | Nothing. | Design only. Flows need connections that a person authorises. The Activator action link is made in the portal. Licences and connector rights are not confirmed. Exporting a flow as a solution may help. That is **unverified** here. |
| Purview sensitivity labels | **Partly**. **Untested.** | The `labels` step sets a label on the model and report. | Only a Fabric administrator can use the bulk-set-labels API. It does not accept a service principal. The label must be in the administrator's label policy. |
| Upload of data to OneLake | **Full**. **Untested.** | Exports and code go to `Files/hb/`. | None. The Fabric CLI can do the same with `fab cp`. |
| Run notebooks and the pipeline | **Full**. **Untested.** | Job Scheduler API with the signed-in user. | Whether notebook jobs accept a service principal is **unverified**. The script uses a user sign-in. |
| Git connection to the team repository | **Manual** | Nothing. | A workspace admin connects the workspace to a branch in the portal. The Git REST API may allow this in code. That is **unverified** here. |

## Manual steps that remain

Do these once per workspace, after a successful run.

1. **Check the capacity.** If `workspace` failed, ask a capacity admin to add you as contributor. Or ask an Azure owner for a capacity.
2. **Set the KQL retention policy.** Open the KQL database `HomeBridgeEvents`. Run this command in a KQL queryset or the explorer:

   ```kusto
   .alter-merge table RawEventsLanding policy retention softdelete = 7d
   ```

   Why: a KQL database Git item cannot hold this command type.
3. **Assign the two report roles.** Open the semantic model security settings. Add members to `General viewer` and `Authorised adviser`. Why: the TMDL holds the roles. It does not hold the members.
4. **Apply a sensitivity label** if the `labels` step did not. Confirm the label name with the tenant admin. Every item with personal data needs a label (blueprint section 06). Why: labels are service metadata, not file content, and only an admin can bulk-set them.
5. **Build the Activator rules** from `action/activator/rules.md`. Sync the item to Git and commit it. After that, the deploy can publish it.
6. **Build the Power Automate flow** from `action/power_automate/review-case-writer.md`. Why: it needs a person to authorise connections.
7. **Provision the SharePoint lists.** Run `action/sharepoint/provision-list.ps1 -WhatIf` first. Why: it is outside Fabric and needs SharePoint rights.
8. **Read the Eventstream custom endpoint** connection details in the portal when you want to send replay events. Set `HB_EVENTSTREAM_CONNECTION` and `HB_EVENTSTREAM_NAME` in your shell. Install `azure-eventhub`. Then run `python -m hbdata replay --target eventstream`. Never commit these values.
9. **Set the pipeline schedule** (proposed: every 15 minutes) on `pl_scheduled_scoring`. It is a target to test, not a platform guarantee.
10. **Measure the 60-second view target.** It is not a Direct Lake guarantee.

If the model fails on first load, wait a few minutes and refresh. The SQL analytics endpoint can lag behind new Delta tables. **Unverified.**

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

### For a service principal (CI/CD later)

| Permission | What it unlocks | Source |
| --- | --- | --- |
| Tenant setting **Service principals can use Fabric APIs** | Any Fabric REST call from a service principal or managed identity | REST: identity support |
| Tenant setting **Service principals can create workspaces, connections, and deployment pipelines** | Create a workspace in code | REST: Create Workspace |
| Capacity contributor for the service principal | Assign a workspace to a capacity | REST: Assign To Capacity |
| A workspace role for the service principal | Publish items and run jobs | Fabric docs |

Limits to know:

- The bulk-set-labels API does not support a service principal. Labels need a human Fabric administrator, or a manual step.
- Each API page says whether it supports a service principal. An item type or job type can refuse one. Check before you move a step to CI.

### For the items outside Fabric

- **SharePoint:** site owner rights, and an Entra app registration for PnP.PowerShell.
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
- No secret patterns appear in the item files. The config loader rejects secret keys.

**Not verified. Treat all of this as untested in Fabric:**

- Any call to a real Fabric, OneLake or Entra endpoint.
- That Fabric accepts the notebook, pipeline, Eventstream, Eventhouse and Environment files as written.
- The Spark behaviour of notebook 06. The Python 3.11 compatibility of the packages (they were tested on 3.13).
- The shape of the job parameters for notebooks and pipelines. It follows the Fabric CLI source and the pipeline REST docs.
- That the Eventstream processed ingestion maps the JSON envelope to `RawEventsLanding`.
- The semantic model on Direct Lake, the SQL endpoint ID, and the model refresh timing.
- Service principal support for notebook jobs.
- Sensitivity labels, SharePoint provisioning, and anything in Power Automate or Activator.

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
- Fabric CLI source (job parameter shape): <https://github.com/microsoft/fabric-cli>
- Copilot skills for Fabric: <https://github.com/microsoft/skills-for-fabric>
