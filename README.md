# Customer Intelligence Engine

**Customer Pulse 360 | Hackathon Challenge 4**

Mortgage Renewal Early Warning is a proposed customer intelligence system on Microsoft Fabric. It joins customer history with live activity, groups accounts, scores churn risk, and sends retention alerts for human review.

**Status:** Proposal sent on 18 October. The build runs from 26 October to 6 November. The team chose Mortgage Renewal Early Warning as the scenario.

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

The dashboard and action work package has no folder yet. It covers the Power BI report, Activator, Power Automate and the SharePoint review-case list.

Each folder README lists its planned items and the rules that apply. The Fabric workspace is not ready yet. Build items that run locally first.

This repository holds the build for now. The team may move the build to a separate repository later.

## Repository contents

- `site/index.html` — current scenario page and proposal.
- `data/`, `kql/`, `notebooks/`, `pipelines/` — build folders. See [Build layout](#build-layout).
- `docs/archive/previous-readme.md` — previous repository overview.
- `docs/archive/technical-blueprint.md` — earlier technical blueprint, retained for reference.

## Publishing

The `Deploy scenario blueprints to GitHub Pages` workflow (`.github/workflows/pages.yml`) publishes `site/` to GitHub Pages. It runs on every push to `main`. You can also run it manually from the Actions tab. Do not put credentials or customer records in `site/`.

## Preview

Open `site/index.html` in a browser. The page loads styling from public CDNs. Connect to the network to see the full page design.
