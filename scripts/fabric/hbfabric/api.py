"""Small Fabric REST client. One credential object, signed in once, serves every call.

Endpoints used (all documented on Microsoft Learn, REST API reference for Fabric):
  GET  /v1/workspaces, POST /v1/workspaces, POST /v1/workspaces/{id}/assignToCapacity
  GET  /v1/capacities
  GET  /v1/workspaces/{id}/items
  POST /v1/workspaces/{id}/items/{id}/jobs/instances?jobType=RunNotebook|Pipeline  and the Location it returns
  POST /v1/admin/items/bulkSetLabels  (Fabric administrator, user identity only)
Not yet run against a real tenant.
"""
from __future__ import annotations

import time
from typing import Callable

import requests

API = "https://api.fabric.microsoft.com"
SCOPE = f"{API}/.default"
TERMINAL = {"Completed", "Failed", "Cancelled", "Deduped"}


class FabricError(RuntimeError):
    pass


class FabricClient:
    def __init__(self, credential, session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep):
        self.credential = credential
        self.session = session or requests.Session()
        self.sleep = sleep

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.credential.get_token(SCOPE).token}", "Content-Type": "application/json"}

    def request(self, method: str, url: str, **kw) -> requests.Response:
        url = url if url.startswith("http") else f"{API}{url}"
        for attempt in range(6):
            resp = self.session.request(method, url, headers=self._headers(), timeout=120, **kw)
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


def _notebook_param(value) -> dict:
    if isinstance(value, bool):
        return {"value": value, "type": "bool"}
    if isinstance(value, int):
        return {"value": value, "type": "int"}
    if isinstance(value, float):
        return {"value": value, "type": "float"}
    return {"value": str(value), "type": "string"}
