# HomeBridge demo portals

Two small local web apps for the demo. HomeBridge is a fictional lender. All data is synthetic.

- **Customer website** (`/customer`). A pretend HomeBridge site. The buttons "Renew my deal" and "I need help with payments" each send one real event to the Fabric Eventstream. The event uses the `live_event` contract (`data/contracts/live_event.schema.json`).
- **Adviser portal** (`/staff`). What HomeBridge staff see: a worklist, a customer page and a summary. Every page says what it shows. Every panel names the Fabric table it reads.

Fabric stays the system of record. The apps keep no copy of the data. Each page reads the Lakehouse SQL endpoint and the Eventhouse when it opens. Results are cached for 15 seconds.

## How the parts fit

```
Customer website ──event──▶ Eventstream HomeBridgeRiskEvents ──▶ Eventhouse RawEvents / ServiceEvents()
                                                                     │
Adviser portal "Process new events" ──starts──▶ Notebook 07_route_review_cases
                                                                     │ routing rules (action/policy)
                                                                     ▼
Adviser portal ◀──reads── Lakehouse: account_scores, feature_attributions, review_cases, review_decisions
```

Notebook 07 writes the review cases. It runs as a Fabric job, so it takes 1 to 3 minutes. In a recorded video you can skip that wait.

## Run it

From the repository root, with the repository virtual environment:

```powershell
.\.venv\Scripts\python -m pip install -r demo\portals\requirements.txt
Copy-Item demo\portals\config.example.yml demo\portals\config.local.yml   # then fill in tenant_id and login_hint
$env:PYTHONPATH = "demo\portals"
.\.venv\Scripts\python -m portals            # live: reads and writes Fabric
.\.venv\Scripts\python -m portals --offline  # rehearsal: local files only
```

Open http://127.0.0.1:8360/. The server listens on this laptop only.

### Live mode

- Sign in with **your own** Microsoft account. The app refuses the shared sandbox account.
- The account needs a Fabric licence and access to the workspace in `config.local.yml`.
- `config.local.yml` holds no secret. Git ignores it anyway.
- The Eventstream connection string is read from Fabric at start-up. It stays in memory. It is never written to disk.
- Before the first live demo, notebook 07 must exist in the workspace (`python scripts/fabric/deploy.py`, see `docs/fabric-deployment.md`).

### Offline mode

- Needs the generated data in `data/output` (`python -m hbdata ...`).
- It reads the parquet files with DuckDB and copies the Eventhouse files to a temporary folder. It runs the same routing code as notebook 07, on this laptop.
- Every page shows a red "Offline preview" banner. Use it to rehearse only. Do not show it as Fabric.

## The demo clock

The synthetic data ends on 15 Oct 2026. The apps use a demo clock, not today's date. The first event is dated 16 Oct 2026 09:00 UTC. Each new event moves the clock on by one hour. This avoids "stale" labels and negative "days left".

## Demo customers

| Customer | Story |
|---|---|
| Alex, HB-M-900001 | Renewal stuck at "documents received". Alex asks for help with payments. A payment-support case opens and goes to the top of the worklist. |
| HB-M-003121 | High score, priority 3. A renewal case is open for adviser ADV-027. |
| Sam, HB-M-900002 | Renewed. No cases. |

## Judging risk

The blueprint (`site/index.html`, section "Out of scope") lists building a custom software application as out of scope. These apps are presenter tools. The scoring, the routing rules and the review cases all run in Fabric. Show the Fabric items (the source links on each page, and the Power BI report) during the demo.

## Tests

```powershell
.\.venv\Scripts\python -m pytest demo\portals\tests -q
```

The tests use offline mode and the generated data. They do not touch Fabric.
