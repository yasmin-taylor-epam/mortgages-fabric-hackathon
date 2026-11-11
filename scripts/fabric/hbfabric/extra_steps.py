"""Optional deploy steps. Each one is off unless its settings section has `enabled: true` or the step is named with --only.

capacity    create a Fabric capacity through Azure Resource Manager (admin list from HB_FABRIC_CAPACITY_ADMINS)
roles       assign workspace roles from the settings
retention   set the soft-delete retention of a KQL table through the Kusto management endpoint
schedule    create or update the schedule of pl_scheduled_scoring (Cron, interval in minutes)
rls         add members to the semantic model roles through the XMLA endpoint (PowerShell + TOM)
sharepoint  create the review-case lists through Microsoft Graph
eventstream read the custom endpoint details of the Eventstream (shows names only, never a key)
replay      send the replay events to the Eventstream, with the connection read at run time
git         connect the workspace to a GitHub branch (GitHub PAT from HB_GITHUB_PAT)

Every step is idempotent: it reads the current state first and changes only what differs.
None of these steps is confirmed in Fabric yet. Where a detail is unverified, the code says so.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import ids, payloads
from .api import FabricError

POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
RLS_SCRIPT = "set_rls_members.ps1"

ENV_CAPACITY_ADMINS = "HB_FABRIC_CAPACITY_ADMINS"
ENV_GITHUB_PAT = "HB_GITHUB_PAT"
ENV_GRAPH = ("HB_GRAPH_CLIENT_ID", "HB_GRAPH_CLIENT_SECRET", "HB_GRAPH_TENANT_ID")


def default_runner(cmd: list[str], env: dict, cwd: str | None = None) -> tuple[int, str]:
    p = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def make_graph_credential(env: dict, default):
    """App-only sign-in when the three HB_GRAPH_* variables are set. Otherwise the signed-in user (delegated)."""
    if all(env.get(k) for k in ENV_GRAPH):
        from azure.identity import ClientSecretCredential

        return ClientSecretCredential(env["HB_GRAPH_TENANT_ID"], env["HB_GRAPH_CLIENT_ID"], env["HB_GRAPH_CLIENT_SECRET"])
    return default


class ExtraSteps:
    """Mixed into Deployer. Uses self.cfg, self.client, self.workspace_id, self.dry_run, self.say, self.credential."""

    env: dict
    runner: object
    graph_credential: object
    eventstream_connection: dict | None = None

    # ----- helpers
    def _need_item(self, item_type: str, name: str, step: str) -> dict | None:
        item = self.client.find_item(self.workspace_id, item_type, name)
        if not item:
            if self.dry_run:
                self.say(f"{item_type} '{name}' does not exist yet. A real run needs the 'core' step first.")
                return None
            raise FabricError(f"{item_type} '{name}' not found. Run the 'core' step before '{step}'.")
        return item

    # ----- capacity
    def capacity(self) -> None:
        c, cc, name = self.client, self.cfg.capacity_create, self.cfg.capacity_name
        url = c.arm_capacity_url(cc.subscription_id, cc.resource_group, name)
        existing = c.arm_get_capacity(url)
        if existing:
            state = (existing.get("properties") or {}).get("state", "unknown")
            self.say(f"Capacity '{name}' already exists in Azure (state {state}). Nothing to create.")
            return
        admins = [a.strip() for a in self.env.get(ENV_CAPACITY_ADMINS, "").split(",") if a.strip()]
        if self.dry_run:
            self.say(f"Would create capacity '{name}' ({cc.sku}, {cc.location}) with {len(admins)} admin(s) from {ENV_CAPACITY_ADMINS}.")
            return
        if not admins:
            raise FabricError(f"Set {ENV_CAPACITY_ADMINS} to a comma-separated list of capacity admins (user principal names or app IDs). They are not read from the settings file.")
        self.say(f"Creating capacity '{name}' ...")
        c.arm_put_capacity(url, payloads.capacity_body(cc, admins))
        c.wait_capacity_active(url)
        for _ in range(20):  # the Fabric API can lag behind Azure
            try:
                c.find_capacity(name)
                break
            except FabricError:
                c.sleep(15)
        self.say(f"Capacity '{name}' is Active.")

    # ----- roles
    def roles(self) -> None:
        if self.dry_run and self.workspace_id.startswith("<"):
            self.say(f"Would assign {len(self.cfg.roles.assignments)} workspace role(s) after the workspace exists.")
            return
        plan = payloads.role_plan(self.cfg.roles.assignments, self.client.list_role_assignments(self.workspace_id))
        for action, a, existing_id in plan:
            label = f"{a['type']} {a['principal_id']} as {a['role']}"
            if action == "keep":
                self.say(f"  role kept: {label}")
            elif self.dry_run:
                self.say(f"  would {action} role: {label}")
            elif action == "add":
                self.client.add_role_assignment(self.workspace_id, a["principal_id"], a["type"], a["role"])
                self.say(f"  role added: {label}")
            else:
                self.client.update_role_assignment(self.workspace_id, existing_id, a["role"])
                self.say(f"  role updated: {label}")

    # ----- retention and streaming (both run Kusto management commands on the KQL database)
    def _kusto_target(self, step: str) -> tuple[str, str] | None:
        db = self._need_item("KQLDatabase", ids.KQL_DATABASE, step)
        if not db:
            return None
        info = self.client.get_kql_database(self.workspace_id, db["id"])
        # Verified on 5 Nov 2026: the Kusto database name of a Fabric KQL database is its item ID, not its display name.
        return info["properties"]["queryServiceUri"], db["id"]

    def streaming(self) -> None:
        """Turn streaming ingestion off for the landing table so that the update policies can read AccountContext."""
        table = payloads.LANDING_TABLE
        if self.dry_run and self.workspace_id.startswith("<"):
            self.say(f"Would turn streaming ingestion off for {table}.")
            return
        target = self._kusto_target("streaming")
        if not target:
            return
        uri, name = target
        rows = self.client.kusto_mgmt(uri, name, f".show table {table} policy streamingingestion")
        if not payloads.streaming_is_enabled(rows):
            self.say(f"Streaming ingestion is already off for {table}.")
        elif self.dry_run:
            self.say(f"Would run on {name}: {payloads.streaming_off_command(table)}")
        else:
            self.client.kusto_mgmt(uri, name, payloads.streaming_off_command(table))
            self.say(f"Turned streaming ingestion off for {table}. Ingestion now uses the queued path, which batches events.")

    def retention(self) -> None:
        r = self.cfg.retention
        command = payloads.retention_command(r.table, r.softdelete)
        if self.dry_run and self.workspace_id.startswith("<"):
            self.say(f"Would run on {ids.KQL_DATABASE}: {command}")
            return
        target = self._kusto_target("retention")
        if not target:
            return
        uri, name = target
        rows = self.client.kusto_mgmt(uri, name, f".show table {r.table} policy retention")
        if payloads.retention_matches(rows, r.softdelete):
            self.say(f"Retention of {r.table} is already {r.softdelete}.")
        elif self.dry_run:
            self.say(f"Would run on {name}: {command}")
        else:
            self.client.kusto_mgmt(uri, name, command)
            self.say(f"Set retention of {r.table} to {r.softdelete}.")

    # ----- schedule
    def schedule(self) -> None:
        s = self.cfg.schedule
        if self.dry_run and self.workspace_id.startswith("<"):
            self.say(f"Would schedule {ids.PIPELINE_SCORING} every {s.interval_minutes} minutes.")
            return
        p = self._need_item("DataPipeline", ids.PIPELINE_SCORING, "schedule")
        if not p:
            return
        existing = [x for x in self.client.list_schedules(self.workspace_id, p["id"], payloads.PIPELINE_JOB_TYPE)
                    if (x.get("configuration") or {}).get("type") == "Cron"]
        if existing and payloads.schedule_matches(existing[0], s):
            self.say(f"{ids.PIPELINE_SCORING} already runs every {s.interval_minutes} minutes.")
            return
        body = payloads.schedule_body(s)
        if self.dry_run:
            self.say(f"Would {'update' if existing else 'create'} a Cron schedule: every {s.interval_minutes} minutes.")
        elif existing:
            self.client.update_schedule(self.workspace_id, p["id"], payloads.PIPELINE_JOB_TYPE, existing[0]["id"], body)
            self.say(f"Updated the schedule of {ids.PIPELINE_SCORING}: every {s.interval_minutes} minutes.")
        else:
            self.client.create_schedule(self.workspace_id, p["id"], payloads.PIPELINE_JOB_TYPE, body)
            self.say(f"Created a schedule for {ids.PIPELINE_SCORING}: every {s.interval_minutes} minutes.")

    # ----- RLS members
    def rls(self) -> None:
        r = self.cfg.rls
        total = sum(len(v) for v in r.members.values())
        if self.dry_run:
            self.say(f"Would add up to {total} {r.member_type} member(s) to {len(r.members)} role(s) of {ids.SEMANTIC_MODEL} through the XMLA endpoint (needs XMLA read-write).")
            return
        ws_name = self.cfg.workspace_name or self.client.get_workspace(self.workspace_id).get("displayName", "")
        script = Path(__file__).resolve().parents[1] / RLS_SCRIPT
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            raise FabricError(f"PowerShell is not installed. Run {script} by hand. See docs/fabric-deployment.md, 'RLS members'.")
        self.build_dir.mkdir(parents=True, exist_ok=True)
        plan = self.build_dir / "rls.members.json"
        plan.write_text(json.dumps(payloads.rls_plan(r, ws_name), indent=2), encoding="utf-8")
        env = dict(self.env)
        env["HB_XMLA_TOKEN"] = self.credential.get_token(POWERBI_SCOPE).token  # in memory only. Never written to disk.
        self.say(f"Adding RLS members to {ids.SEMANTIC_MODEL} through the XMLA endpoint ...")
        code, out = self.runner([shell, "-NoProfile", "-File", str(script), "-PlanPath", str(plan)], env)
        for line in out.strip().splitlines()[-12:]:
            self.say(f"  {line}")
        if code != 0:
            raise FabricError("The RLS script failed. Check that XMLA read-write is on for the capacity, then use the manual step in docs/fabric-deployment.md.")

    # ----- SharePoint
    def sharepoint(self) -> None:
        s, c = self.cfg.sharepoint, self.client
        hostname, path = payloads.site_parts(s.site_url)
        cred = self.graph_credential
        site = c.graph_get_site(hostname, path, cred)
        lists = {x["displayName"]: x for x in c.graph_lists(site["id"], cred)}
        for spec in payloads.sharepoint_lists(self.repo / s.schema_path):
            title = spec["title"]
            if title not in lists:
                if self.dry_run:
                    self.say(f"  would create list '{title}' with {len(spec['columns'])} columns.")
                else:
                    c.graph_create_list(site["id"], payloads.list_body(spec), cred)
                    self.say(f"  list created: '{title}'.")
                continue
            have = {col["name"] for col in c.graph_columns(site["id"], lists[title]["id"], cred)}
            missing = [col for col in spec["columns"] if col["name"] not in have]
            for col in missing:
                if self.dry_run:
                    self.say(f"  would add column '{col['name']}' to '{title}'.")
                else:
                    c.graph_add_column(site["id"], lists[title]["id"], payloads.column_body(col), cred)
                    self.say(f"  column added: '{title}'.{col['name']}")
            if not missing:
                self.say(f"  list '{title}' already has all {len(spec['columns'])} columns.")
        self.say("SharePoint permissions are not set by this step. A site owner must secure each list (see action/README.md).")

    # ----- Eventstream and replay
    def _custom_endpoint(self) -> dict:
        es = self._need_item("Eventstream", ids.EVENTSTREAM, "eventstream")
        if not es:
            return {}
        sources = [x for x in self.client.eventstream_topology(self.workspace_id, es["id"]).get("sources", []) if x.get("type") == "CustomEndpoint"]
        if not sources:
            raise FabricError("The Eventstream has no custom endpoint source. Publish it with the 'core' step.")
        conn = self.client.eventstream_source_connection(self.workspace_id, es["id"], sources[0]["id"])
        keys = conn.get("accessKeys") or {}
        self.eventstream_connection = {"namespace": conn.get("fullyQualifiedNamespace", ""), "name": conn.get("eventHubName", ""),
                                       "connection": keys.get("primaryConnectionString", "")}
        return self.eventstream_connection

    def eventstream(self) -> None:
        if self.dry_run and self.workspace_id.startswith("<"):
            self.say("Would read the custom endpoint details of the Eventstream.")
            return
        info = self._custom_endpoint()
        if info:
            self.say(f"Eventstream custom endpoint: namespace {info['namespace']}, hub {info['name']}. The key is held in memory only. Run the 'replay' step to use it.")

    def replay(self) -> None:
        rp = self.cfg.replay
        data_dir = self.repo / "data"
        args = ["--target", "eventstream"] + (["--fixture", str(self.repo / rp.fixture)] if rp.fixture and not rp.backfill else ["--backfill"])
        if self.dry_run:
            self.say(f"Would read the Eventstream connection and run: python -m hbdata replay {' '.join(args)}")
            return
        if not (data_dir / "output" / "eventhouse" / "account_context.parquet").exists():
            raise FabricError("Replay needs the local data in data/output. Run scripts/run_local_flow.ps1 or `python -m hbdata all` first.")
        info = self.eventstream_connection or self._custom_endpoint()
        if not info.get("connection"):
            raise FabricError("The Eventstream returned no connection string.")
        env = dict(self.env)
        env["HB_EVENTSTREAM_CONNECTION"], env["HB_EVENTSTREAM_NAME"] = info["connection"], info["name"]  # child process only
        self.say("Sending the replay events to the Eventstream ...")
        code, out = self.runner([sys.executable, "-m", "hbdata", "replay", *args], env, str(data_dir))
        self.say(f"  {out.strip().splitlines()[-1] if out.strip() else 'no output'}")
        if code != 0:
            raise FabricError("The replay command failed. Is azure-eventhub installed (pip install azure-eventhub)?")

    # ----- Git
    def git(self) -> None:
        g, c = self.cfg.git, self.client
        if self.dry_run and self.workspace_id.startswith("<"):
            self.say(f"Would connect the workspace to GitHub {g.owner}/{g.repository}@{g.branch} and initialise it ({g.initialization_strategy}).")
            return
        state = c.git_connection(self.workspace_id).get("gitConnectionState", "NotConnected")
        if state == "ConnectedAndInitialized":
            self.say("The workspace is already connected to Git and initialised.")
            return
        if state == "NotConnected":
            conn_id = g.connection_id or next((x["id"] for x in c.list_connections() if x.get("displayName") == g.connection_name), "")
            pat = self.env.get(ENV_GITHUB_PAT, "")
            if not conn_id and not pat:
                if self.dry_run:
                    self.say(f"Would need {ENV_GITHUB_PAT} to create the connection '{g.connection_name}'.")
                    return
                raise FabricError(f"No Fabric connection '{g.connection_name}' exists. Set {ENV_GITHUB_PAT} to a GitHub PAT for this run, or set git.connection_id.")
            if self.dry_run:
                self.say(f"Would {'use connection ' + conn_id if conn_id else 'create connection ' + g.connection_name}, connect and initialise.")
                return
            if not conn_id:
                conn_id = c.create_connection(payloads.github_connection_body(g, pat))["id"]
                self.say(f"Created the Fabric connection '{g.connection_name}'.")
            c.git_connect(self.workspace_id, payloads.git_connect_body(g, conn_id))
            self.say(f"Connected to GitHub {g.owner}/{g.repository}@{g.branch}.")
        elif self.dry_run:
            self.say("The workspace is connected but not initialised. Would initialise it.")
            return
        result = c.git_initialize(self.workspace_id, g.initialization_strategy)
        action = result.get("requiredAction", "None")
        if action == "UpdateFromGit":
            c.git_update_from_git(self.workspace_id, result.get("remoteCommitHash", ""), result.get("workspaceHead"), g.initialization_strategy)
            self.say("Updated the workspace from Git.")
        elif action == "CommitToGit":
            self.say("The workspace holds items that the branch does not. This step does not commit. Commit them in the portal.")
        else:
            self.say("Git initialised. No further action needed.")
