# Copilot instructions for Customer Pulse 360

## Hosted scenario document

- The scenario blueprint document is `site/index.html`.
- Its hosted copy is on GitHub Pages at https://yasmin-taylor-epam.github.io/customer-pulse-360/. The page is public.
- The workflow `.github/workflows/pages.yml` publishes the `site/` folder. It runs on every push to `main` and on manual dispatch.
- Do not publish the page by hand. Do not publish to Lavish or any other host. Merge changes to `main` instead.
- Never put credentials, secrets, or customer records in `site/`.
- To verify the hosted copy after a deploy, check the workflow run and fetch the page:

```powershell
gh run list -R yasmin-taylor-epam/customer-pulse-360 -w pages.yml -L 1
curl.exe -s -o NUL -w "%{http_code}" https://yasmin-taylor-epam.github.io/customer-pulse-360/
```

## Build workspace

- The build lives in `data/`, `kql/`, `notebooks/`, `pipelines/`, `action/` and `reports/`. The README "Build layout" section names the owner of each folder.
- Each work package (data and replay, models and evaluation, dashboard and action) runs in its own session. Change only the files that your work package owns.
- The invariants INV-1 to INV-6 and the failure cases T01 to T07 in `site/index.html` are the acceptance rules. Do not weaken them.
- Use synthetic data only. Never commit real customer records, generated output in `data/output/`, credentials or connection secrets.
- The Fabric workspace is not ready yet. Do not claim that an item runs in Fabric until someone has run it there.

## Writing style

- Write reader-facing text in clear, concise British English.
- Follow ASD-STE100 structural rules: short sentences, active voice, one idea per sentence.
- Keep chat replies terse (caveman style). Keep every fact, number, and negation.

## Content rules

- Label fictional companies, illustrative cases, and proposed targets as such.
- Cite a source for every market or regulatory fact. Do not invent figures.
