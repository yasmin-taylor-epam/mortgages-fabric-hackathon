"""Small Fabric REST client. One credential object, signed in once, serves every call.

Endpoints used (all documented on Microsoft Learn, REST API reference for Fabric):
  GET  /v1/workspaces, POST /v1/workspaces, POST /v1/workspaces/{id}/assignToCapacity
  GET  /v1/capacities
  GET  /v1/workspaces/{id}/items
  POST /v1/workspaces/{id}/items/{id}/jobs/instances?jobType=RunNotebook|Pipeline  and the Location it returns
  POST /v1/admin/items/bulkSetLabels  (Fabric administrator, user identity only)
  Optional steps: roleAssignments, job schedules, Git connect/initialize/updateFromGit, connections, Eventstream topology
  and source connection, KQL database, Kusto /v1/rest/mgmt, ARM Microsoft.Fabric/capacities, Microsoft Graph lists.
Not yet run against a real tenant.
"""
from __future__ import annotations

import time
from typing import Callable

import requests

API = "https://api.fabric.microsoft.com"
SCOPE = f"{API}/.default"
KUSTO_SCOPE = "https://api.kusto.windows.net/.default"  # UNVERIFIED for a Fabric eventhouse. Documented for Kusto in general.
ARM = "https://management.azure.com"
ARM_SCOPE = f"{ARM}/.default"
ARM_CAPACITY_API = "2023-11-01"
GRAPH = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TERMINAL = {"Completed", "Failed", "Cancelled", "Deduped"}


class FabricError(RuntimeError):
    pass


class FabricClient:
    def __init__(self, credential, session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep):
        self.credential = credential
        self.session = session or requests.Session()
        self.sleep = sleep

    def _headers(self, scope: str = SCOPE) -> dict:
        return {"Authorization": f"Bearer {self.credential.get_token(scope).token}", "Content-Type": "application/json"}

    def request(self, method: str, url: str, scope: str = SCOPE, **kw) -> requests.Response:
        url = url if url.startswith("http") else f"{API}{url}"
        for attempt in range(6):
            resp = self.session.request(method, url, headers=self._headers(scope), timeout=120, **kw)
            if resp.status_code == 429:
                self.sleep(float(resp.headers.get("Retry-After", 5 * (attempt + 1))))
                continue
            if resp.status_code >= 400:
                raise FabricError(f"{method} {url} -> {resp.status_code}: {resp.text[:500]}")
            return resp
        raise FabricError(f"{method} {url}: too many 429 responses")

    def paged(self, url: str) -> list[dict]:
        out, token = [], None
        while True:
            sep = "&" if "?" in url else "?"
            body = self.request("GET", url + (f"{sep}continuationToken={token}" if token else "")).json()
            out += body.get("value", [])
            token = body.get("continuationToken")
            if not token:
                return out

    def wait_operation(self, resp: requests.Response, poll_seconds: float = 5.0, timeout_seconds: float = 3600) -> dict:
        """Follow a long-running operation (202 with a Location header) until it ends."""
        if resp.status_code != 202:
            return resp.json() if resp.content else {}
        url, waited = resp.headers["Location"], 0.0
        wait = float(resp.headers.get("Retry-After", poll_seconds))
        while waited <= timeout_seconds:
            self.sleep(wait)
            waited += wait
            body = self.request("GET", url).json()
            state = body.get("status")
            if state in TERMINAL or state in {"Succeeded", "Failed"}:
                return body
            wait = poll_seconds
        raise FabricError(f"Timed out waiting for {url}")

    # ----- workspaces and capacities
    def find_workspace(self, name: str) -> dict | None:
        hits = [w for w in self.paged("/v1/workspaces") if w.get("displayName") == name]
        return hits[0] if hits else None

    def get_workspace(self, workspace_id: str) -> dict:
        return self.request("GET", f"/v1/workspaces/{workspace_id}").json()

    def find_capacity(self, name: str) -> dict:
        hits = [c for c in self.paged("/v1/capacities") if c.get("displayName") == name]
        if not hits:
            raise FabricError(f"No capacity named '{name}' that you can see. Check the name, or ask a capacity admin to add you as contributor.")
        return hits[0]

    def create_workspace(self, name: str, capacity_id: str | None) -> dict:
        body = {"displayName": name}
        if capacity_id:
            body["capacityId"] = capacity_id
        return self.request("POST", "/v1/workspaces", json=body).json()

    def assign_capacity(self, workspace_id: str, capacity_id: str) -> None:
        resp = self.request("POST", f"/v1/workspaces/{workspace_id}/assignToCapacity", json={"capacityId": capacity_id})
        self.wait_operation(resp)

    # ----- items
    def list_items(self, workspace_id: str) -> list[dict]:
        return self.paged(f"/v1/workspaces/{workspace_id}/items")

    def find_item(self, workspace_id: str, item_type: str, name: str) -> dict | None:
        hits = [i for i in self.list_items(workspace_id) if i.get("type") == item_type and i.get("displayName") == name]
        return hits[0] if hits else None

    # ----- jobs
    def run_job(self, workspace_id: str, item_id: str, job_type: str, execution_data: dict | None = None,
                timeout_minutes: int = 60) -> dict:
        body = {"executionData": execution_data} if execution_data else None
        resp = self.request("POST", f"/v1/workspaces/{workspace_id}/items/{item_id}/jobs/instances?jobType={job_type}", json=body)
        result = self.wait_operation(resp, timeout_seconds=timeout_minutes * 60)
        if result.get("status") != "Completed":
            raise FabricError(f"Job {job_type} ended as {result.get('status')}: {result.get('failureReason')}")
        return result

    def run_notebook(self, workspace_id: str, item_id: str, params: dict | None = None, timeout_minutes: int = 60) -> dict:
        data = {"parameters": {k: _notebook_param(v) for k, v in (params or {}).items()}} if params else None
        return self.run_job(workspace_id, item_id, "RunNotebook", data, timeout_minutes)

    def run_pipeline(self, workspace_id: str, item_id: str, params: dict | None = None, timeout_minutes: int = 60) -> dict:
        data = {"parameters": dict(params)} if params else None
        return self.run_job(workspace_id, item_id, "Pipeline", data, timeout_minutes)

    # ----- labels
    def set_labels(self, label_id: str, items: list[dict]) -> list[dict]:
        body = {"items": [{"id": i["id"], "type": i["type"]} for i in items], "labelId": label_id, "assignmentMethod": "Standard"}
        return self.request("POST", "/v1/admin/items/bulkSetLabels", json=body).json().get("itemsChangeLabelStatus", [])

    # ----- workspace roles
    def list_role_assignments(self, workspace_id: str) -> list[dict]:
        return self.paged(f"/v1/workspaces/{workspace_id}/roleAssignments")

    def add_role_assignment(self, workspace_id: str, principal_id: str, principal_type: str, role: str) -> None:
        self.request("POST", f"/v1/workspaces/{workspace_id}/roleAssignments",
                     json={"principal": {"id": principal_id, "type": principal_type}, "role": role})

    def update_role_assignment(self, workspace_id: str, assignment_id: str, role: str) -> None:
        self.request("PATCH", f"/v1/workspaces/{workspace_id}/roleAssignments/{assignment_id}", json={"role": role})

    # ----- schedules
    def list_schedules(self, workspace_id: str, item_id: str, job_type: str) -> list[dict]:
        return self.paged(f"/v1/workspaces/{workspace_id}/items/{item_id}/jobs/{job_type}/schedules")

    def create_schedule(self, workspace_id: str, item_id: str, job_type: str, body: dict) -> dict:
        return self.request("POST", f"/v1/workspaces/{workspace_id}/items/{item_id}/jobs/{job_type}/schedules", json=body).json()

    def update_schedule(self, workspace_id: str, item_id: str, job_type: str, schedule_id: str, body: dict) -> dict:
        return self.request("PATCH", f"/v1/workspaces/{workspace_id}/items/{item_id}/jobs/{job_type}/schedules/{schedule_id}", json=body).json()

    # ----- Git integration
    def git_connection(self, workspace_id: str) -> dict:
        return self.request("GET", f"/v1/workspaces/{workspace_id}/git/connection").json()

    def list_connections(self) -> list[dict]:
        return self.paged("/v1/connections")

    def create_connection(self, body: dict) -> dict:
        return self.request("POST", "/v1/connections", json=body).json()

    def git_connect(self, workspace_id: str, body: dict) -> None:
        self.request("POST", f"/v1/workspaces/{workspace_id}/git/connect", json=body)

    def git_initialize(self, workspace_id: str, strategy: str) -> dict:
        resp = self.request("POST", f"/v1/workspaces/{workspace_id}/git/initializeConnection", json={"initializationStrategy": strategy})
        if resp.status_code != 202:
            return resp.json() if resp.content else {}
        self.wait_operation(resp)
        return self.request("GET", f"{resp.headers['Location'].rstrip('/')}/result").json()  # UNVERIFIED: result URL of the long-running call

    def git_update_from_git(self, workspace_id: str, remote_commit_hash: str, workspace_head: str | None, policy: str | None = None) -> None:
        body: dict = {"remoteCommitHash": remote_commit_hash, "options": {"allowOverrideItems": True}}
        if workspace_head:
            body["workspaceHead"] = workspace_head
        if policy and policy != "None":
            body["conflictResolution"] = {"conflictResolutionType": "Workspace", "conflictResolutionPolicy": policy}
        self.wait_operation(self.request("POST", f"/v1/workspaces/{workspace_id}/git/updateFromGit", json=body))

    # ----- Eventstream and KQL database
    def eventstream_topology(self, workspace_id: str, eventstream_id: str) -> dict:
        return self.request("GET", f"/v1/workspaces/{workspace_id}/eventstreams/{eventstream_id}/topology").json()

    def eventstream_source_connection(self, workspace_id: str, eventstream_id: str, source_id: str) -> dict:
        return self.request("GET", f"/v1/workspaces/{workspace_id}/eventstreams/{eventstream_id}/sources/{source_id}/connection").json()

    def get_kql_database(self, workspace_id: str, database_id: str) -> dict:
        return self.request("GET", f"/v1/workspaces/{workspace_id}/kqlDatabases/{database_id}").json()

    def kusto_mgmt(self, query_uri: str, database: str, command: str) -> list[list]:
        """Run one management command on the Kusto endpoint of a KQL database. Returns the rows of the first table.

        The token audience for a Fabric eventhouse is UNVERIFIED. The call tries the general Kusto audience first.
        After a 401 or 403 it tries the query URI of the database as the audience.
        """
        base = query_uri.rstrip("/")
        last: FabricError | None = None
        for scope in (KUSTO_SCOPE, f"{base}/.default"):
            try:
                resp = self.request("POST", f"{base}/v1/rest/mgmt", scope=scope, json={"db": database, "csl": command})
            except FabricError as exc:
                if " -> 401" not in str(exc) and " -> 403" not in str(exc):
                    raise
                last = exc
                continue
            tables = resp.json().get("Tables") or []
            return tables[0].get("Rows", []) if tables else []
        raise last

    # ----- Azure Resource Manager (capacity)
    def arm_capacity_url(self, subscription_id: str, resource_group: str, name: str) -> str:
        return (f"{ARM}/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
                f"/providers/Microsoft.Fabric/capacities/{name}?api-version={ARM_CAPACITY_API}")

    def arm_get_capacity(self, url: str) -> dict | None:
        try:
            return self.request("GET", url, scope=ARM_SCOPE).json()
        except FabricError as exc:
            if " -> 404" in str(exc):
                return None
            raise

    def arm_put_capacity(self, url: str, body: dict) -> dict:
        return self.request("PUT", url, scope=ARM_SCOPE, json=body).json()

    def wait_capacity_active(self, url: str, poll_seconds: float = 15, timeout_seconds: float = 1800) -> dict:
        waited = 0.0
        while waited <= timeout_seconds:
            cap = self.arm_get_capacity(url) or {}
            state = (cap.get("properties") or {}).get("state")
            if state == "Active":
                return cap
            if state in {"Failed", "Deleting"}:
                raise FabricError(f"The capacity ended in state {state}.")
            self.sleep(poll_seconds)
            waited += poll_seconds
        raise FabricError("Timed out waiting for the capacity to become Active.")

    # ----- Microsoft Graph (SharePoint lists)
    def graph_get_site(self, hostname: str, path: str, token_credential=None) -> dict:
        return self._graph("GET", f"{GRAPH}/sites/{hostname}:/{path}", token_credential)

    def graph_lists(self, site_id: str, token_credential=None) -> list[dict]:
        out, url = [], f"{GRAPH}/sites/{site_id}/lists?$select=id,displayName"
        while url:
            body = self._graph("GET", url, token_credential)
            out += body.get("value", [])
            url = body.get("@odata.nextLink")
        return out

    def graph_columns(self, site_id: str, list_id: str, token_credential=None) -> list[dict]:
        return self._graph("GET", f"{GRAPH}/sites/{site_id}/lists/{list_id}/columns", token_credential).get("value", [])

    def graph_create_list(self, site_id: str, body: dict, token_credential=None) -> dict:
        return self._graph("POST", f"{GRAPH}/sites/{site_id}/lists", token_credential, json=body)

    def graph_add_column(self, site_id: str, list_id: str, body: dict, token_credential=None) -> dict:
        return self._graph("POST", f"{GRAPH}/sites/{site_id}/lists/{list_id}/columns", token_credential, json=body)

    def _graph(self, method: str, url: str, token_credential=None, **kw) -> dict:
        saved = self.credential
        self.credential = token_credential or saved
        try:
            resp = self.request(method, url, scope=GRAPH_SCOPE, **kw)
        finally:
            self.credential = saved
        return resp.json() if resp.content else {}


def _notebook_param(value) -> dict:
    if isinstance(value, bool):
        return {"value": value, "type": "bool"}
    if isinstance(value, int):
        return {"value": value, "type": "int"}
    if isinstance(value, float):
        return {"value": value, "type": "float"}
    return {"value": str(value), "type": "string"}
