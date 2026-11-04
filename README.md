# Customer Intelligence Engine

**Customer Pulse 360 | Hackathon Challenge 4**

Mortgage Renewal Early Warning is a proposed customer intelligence system on Microsoft Fabric. It joins customer history with live activity, groups accounts, scores churn risk, and sends retention alerts for human review.

**Status:** Proposal sent on 18 October. The team chose Mortgage Renewal Early Warning as the scenario. The three work packages (data and replay, models and evaluation, dashboard and action) are built and integrated. They run and pass tests locally on synthetic data. Nothing has run in Fabric yet. See [Run the whole flow locally](#run-the-whole-flow-locally).

## Review the proposal

- [Open the scenario page](site/index.html) for the challenge, chosen scenario, and design.
- [Open the hosted review copy](https://yasmin-taylor-epam.github.io/customer-pulse-360/) on GitHub Pages. The page is public.

The scenario page is the current source for the project. This repository does not contain a deployed Customer 360 service or production churn model.

## Chosen scenario and archived alternative

The chosen scenario uses a fictional mortgage provider, **HomeBridge**, to illustrate the idea.

- **HomeBridge** is a mortgage lender. The proposal identifies external refinancing risk before a fixed-rate deal ends. It routes payment difficulties to support before any commercial retention action.
- **Archived alternative:** HavenCover is a fictional home insurer. The proposal identifies non-renewal risk before a policy expires. It investigates claim and repair problems before any commercial action.

## Shared challenge

The challenge asks for four capabilities:

1. A real-time Customer 360 view that joins live activity with customer history.
2. Machine-learning customer segments.
3. A churn-risk model with drivers, aimed at a warning about 90 days before renewal.
4. A retention alert that responds to live events and reaches a person.

The scenario page maps these goals to Microsoft Fabric tools. The suggested tools are not mandatory components.

## Evidence limits

- A 90-day prediction horizon does not prove a 90-day early warning. Score the customer at a fixed point before renewal. Freeze the available information. Then evaluate the later renewal outcome.
- Feature attributions explain how model inputs affect a score. They do not prove the customer's true motivation or that an action will work.
- Synthetic results can show that a workflow works. They cannot prove improved retention. That claim needs evidence from real actions and a comparison group.

## Build layout

The build follows the three work packages in the blueprint (section 06). Each work package can run in its own Copilot session. A session changes only the files that its work package owns.

| Folder | Contents | Owner |
| --- | --- | --- |
| `data/` | Data contracts, synthetic data generator and replay fixtures. | Data and replay |
| `kql/` | Eventhouse tables, ingestion mappings and journey queries. | Data and replay |
| `notebooks/` | Validation, feature snapshot, training, scoring and evaluation notebooks. | Shared. Each notebook has one owner. |
| `pipelines/` | Fabric pipeline definitions. | Shared. Each pipeline has one owner. |
| `action/` | Activator rules, Power Automate flows and the SharePoint review-case list. | Dashboard and action |
| `reports/` | Power BI semantic model and mortgage report. | Dashboard and action |
| `scripts/` | Local runner for the whole flow. | Integration |
| `fabric/` | Fabric Git-format items, and the settings file for the deploy script. Notebooks and the KQL schema in it are generated. | Integration |
| `scripts/fabric/` | Deploy script for Microsoft Fabric (fabric-cicd and the Fabric REST API) and its tests. | Integration |

Each folder README lists its contents, status and rules. The Fabric workspace is not ready yet. Every item runs locally first. Do not claim that an item runs in Fabric until someone has run it there.

## Run the whole flow locally

`scripts/run_local_flow.ps1` deletes `data/output/` and rebuilds it. It generates and validates synthetic data, trains and evaluates the models, scores one checkpoint run, copies the context, replays events into the local case writer, and runs the test suite of each work package.

```powershell
powershell -File scripts\run_local_flow.ps1            # flow and all tests (several minutes)
powershell -File scripts\run_local_flow.ps1 -Demo      # then add a scheduled run and a failed run (T05 demo)
```

Run `-Demo` last. Its extra live runs flag more accounts than the capacity of ten reviews per run. Then `action/tests/test_action_data_integration.py` fails until you rebuild without `-Demo`. The local flow uses a SQLite file in place of SharePoint. It needs Python (tested with 3.13).

This repository holds the build for now. The team may move the build to a separate repository later.

## Deploy to Fabric as code

`scripts/deploy_fabric.ps1` creates or targets a workspace, deploys the items, uploads the synthetic data, and runs the notebooks and the scoring pipeline. Opt-in steps (off by default) can also create a capacity, assign workspace roles, set the KQL retention, schedule the pipeline, add role members, create the SharePoint lists, read the Eventstream endpoint for replay, and connect the workspace to GitHub. You sign in once. It has an offline `--validate` mode and a read-only `--dry-run` mode. It is tested offline only. Nobody has run it in Fabric.

```powershell
powershell -File scripts\deploy_fabric.ps1 --validate   # offline, no sign-in
```

[docs/fabric-deployment.md](docs/fabric-deployment.md) lists what is automated, what stays manual and why, the exact steps, and the permissions to request.

## Repository contents

- `site/index.html` — current scenario page and proposal.
- `data/`, `kql/`, `notebooks/`, `pipelines/`, `action/`, `reports/`, `scripts/`, `fabric/` — build folders. See [Build layout](#build-layout).
- `docs/fabric-deployment.md` — Fabric deployment as code: what is automated, what is manual, steps and permissions.
- `docs/archive/previous-readme.md` — previous repository overview.
- `docs/archive/technical-blueprint.md` — earlier technical blueprint, retained for reference.

## Publishing

The `Deploy scenario blueprints to GitHub Pages` workflow (`.github/workflows/pages.yml`) publishes `site/` to GitHub Pages. It runs on every push to `main`. You can also run it manually from the Actions tab. Do not put credentials or customer records in `site/`.

## Preview

Open `site/index.html` in a browser. The page loads styling from public CDNs. Connect to the network to see the full page design.
