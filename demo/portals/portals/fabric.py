"""Live connections to Fabric: sign-in, Lakehouse SQL endpoint, Eventhouse KQL, Eventstream and the notebook job.

Nothing here stores data. Each call reads Fabric at the time of the call. The Eventstream key is read from the
Fabric API and kept in memory only. The Eventstream sender stays open between clicks.
"""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .config import REPO, Settings, check_account

sys.path.insert(0, str(REPO / "scripts" / "fabric"))
from hbfabric import ids  # noqa: E402
from hbfabric.api import KUSTO_SCOPE, SCOPE, FabricClient, FabricError, _notebook_param  # noqa: E402

CACHE_NAME = "homebridge-demo-portals"  # a separate token cache. It is not the deploy script cache
PORTAL_URL = "https://app.fabric.microsoft.com"
ITEM_PATHS = {"Lakehouse": "lakehouses", "Eventhouse": "eventhouses", "KQLDatabase": "databases",
              "Eventstream": "eventstreams", "Report": "reports", "Notebook": "synapsenotebooks",
              "DataPipeline": "pipelines", "SemanticModel": "datasets"}


def make_credential(settings: Settings):
    from azure.identity import (AzureCliCredential, DeviceCodeCredential, InteractiveBrowserCredential,
                                TokenCachePersistenceOptions)
    cache = TokenCachePersistenceOptions(name=CACHE_NAME)
    tenant = settings.tenant_id or None
    if settings.auth == "azcli":
        return AzureCliCredential(tenant_id=tenant)
    if settings.auth == "device":
        return DeviceCodeCredential(tenant_id=tenant, cache_persistence_options=cache)
    return InteractiveBrowserCredential(tenant_id=tenant, login_hint=settings.login_hint or None,
                                        cache_persistence_options=cache)


def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


@dataclass
class Item:
    id: str
    name: str
    type: str


class Fabric:
    """One Fabric workspace. Item IDs are looked up once. Data is read on every call."""

    def __init__(self, settings: Settings, credential=None, client: FabricClient | None = None):
        self.settings = settings
        self.credential = credential or make_credential(settings)
        self.client = client or FabricClient(self.credential)
        self._lock = threading.Lock()
        self._ready = False
        self.workspace_id = ""
        self.items: dict[str, Item] = {}
        self.sql_host = ""
        self.kusto_uri = ""
        self.kusto_db = ""
        self._eventstream: dict | None = None
        self._producer = None
        self._send_lock = threading.Lock()

    # ----- discovery
    def connect(self) -> "Fabric":
        with self._lock:
            if self._ready:
                return self
            token = self.credential.get_token(SCOPE)
            check_account(_token_account(token.token), self.settings.allow_lab_account)
            ws = self.client.find_workspace(self.settings.workspace_name)
            if not ws:
                raise FabricError(f"You cannot see a workspace named '{self.settings.workspace_name}'.")
            self.workspace_id = ws["id"]
            wanted = {("Lakehouse", ids.LAKEHOUSE), ("Eventhouse", ids.EVENTHOUSE), ("KQLDatabase", ids.KQL_DATABASE),
                      ("Eventstream", self.settings.eventstream_name), ("Report", ids.SEMANTIC_MODEL), ("Notebook", self.settings.notebook_name),
                      ("DataPipeline", ids.PIPELINE_SCORING), ("SemanticModel", ids.SEMANTIC_MODEL)}
            for it in self.client.list_items(self.workspace_id):
                if (it.get("type"), it.get("displayName")) in wanted:
                    self.items[it["type"]] = Item(it["id"], it["displayName"], it["type"])
            if "Lakehouse" in self.items:
                lh = self.client.request("GET", f"/v1/workspaces/{self.workspace_id}/lakehouses/{self.items['Lakehouse'].id}").json()
                self.sql_host = ((lh.get("properties") or {}).get("sqlEndpointProperties") or {}).get("connectionString", "")
            if "KQLDatabase" in self.items:
                db = self.client.get_kql_database(self.workspace_id, self.items["KQLDatabase"].id)
                self.kusto_uri = db["properties"]["queryServiceUri"].rstrip("/")
                self.kusto_db = self.items["KQLDatabase"].id  # the Kusto database name is the item ID
            self._ready = True
            return self

    def link(self, item_type: str) -> str:
        item = self.items.get(item_type)
        if not item or not self.workspace_id:
            return ""
        if item_type == "KQLDatabase" and "Eventhouse" in self.items:
            return f"{PORTAL_URL}/groups/{self.workspace_id}/eventhouses/{self.items['Eventhouse'].id}/databases/{item.id}"
        return f"{PORTAL_URL}/groups/{self.workspace_id}/{ITEM_PATHS[item_type]}/{item.id}"

    def workspace_link(self) -> str:
        return f"{PORTAL_URL}/groups/{self.workspace_id}" if self.workspace_id else ""

    # ----- Lakehouse SQL analytics endpoint (read only)
    def sql(self, query: str, params: tuple = ()) -> list[dict]:
        self.connect()
        if not self.sql_host:
            raise FabricError("The Lakehouse has no SQL analytics endpoint yet.")
        import mssql_python
        conn = mssql_python.connect(f"Server={self.sql_host},1433;Database={ids.LAKEHOUSE};Encrypt=yes;",
                                    token_provider=self.credential)
        try:
            cur = conn.cursor()
            cur.execute(query, params)
            cols = [c[0] for c in cur.description]
            return [{k: _plain(v) for k, v in zip(cols, row)} for row in cur.fetchall()]
        finally:
            conn.close()

    # ----- Eventhouse KQL (read only)
    def kql(self, query: str) -> list[dict]:
        self.connect()
        if not self.kusto_uri:
            raise FabricError("The workspace has no KQL database named HomeBridgeEvents.")
        last: Exception | None = None
        for scope in (KUSTO_SCOPE, f"{self.kusto_uri}/.default"):
            try:
                resp = self.client.request("POST", f"{self.kusto_uri}/v1/rest/query", scope=scope,
                                           json={"db": self.kusto_db, "csl": query})
            except FabricError as exc:
                if " -> 401" not in str(exc) and " -> 403" not in str(exc):
                    raise
                last = exc
                continue
            tables = resp.json().get("Tables") or []
            if not tables:
                return []
            cols = [c["ColumnName"] for c in tables[0]["Columns"]]
            return [dict(zip(cols, row)) for row in tables[0]["Rows"]]
        raise last

    # ----- Eventstream (write: one event at a time)
    def _eventstream_endpoint(self) -> dict:
        if self._eventstream is None:
            self.connect()
            es = self.items.get("Eventstream")
            if not es:
                raise FabricError(f"The workspace has no Eventstream named {self.settings.eventstream_name}.")
            sources = [s for s in self.client.eventstream_topology(self.workspace_id, es.id).get("sources", [])
                       if s.get("type") == "CustomEndpoint"]
            if not sources:
                raise FabricError("The Eventstream has no custom endpoint source.")
            conn = self.client.eventstream_source_connection(self.workspace_id, es.id, sources[0]["id"])
            self._eventstream = {"name": conn.get("eventHubName", ""),
                                 "connection": (conn.get("accessKeys") or {}).get("primaryConnectionString", "")}
        return self._eventstream

    def _get_producer(self):
        """One sender, kept open between clicks. Opening a new one for each click took several seconds."""
        if self._producer is None:
            from azure.eventhub import EventHubProducerClient
            ep = self._eventstream_endpoint()
            self._producer = EventHubProducerClient.from_connection_string(ep["connection"], eventhub_name=ep["name"])
        return self._producer

    def _drop_producer(self) -> None:
        producer, self._producer = self._producer, None
        if producer is not None:
            try:
                producer.close()
            except Exception:  # noqa: BLE001 - the connection is already broken
                pass

    def send_event(self, envelope: dict) -> None:
        from azure.eventhub import EventData
        body = {k: v for k, v in envelope.items() if k != "simulated_ingested_at"}
        with self._send_lock:
            for attempt in (1, 2):
                producer = self._get_producer()
                try:
                    batch = producer.create_batch()
                    batch.add(EventData(json.dumps(body)))
                    producer.send_batch(batch)
                    return
                except Exception:
                    self._drop_producer()  # a stale connection: open a new one and try once more
                    if attempt == 2:
                        raise

    def warm_up(self) -> None:
        """Do the slow first steps before the demo starts: sign-in, the first SQL connection and the Eventstream sender."""
        self.connect()
        if self.sql_host:
            self.sql("SELECT 1 AS ok")
        if "Eventstream" in self.items:
            with self._send_lock:
                producer = self._get_producer()
                producer.get_eventhub_properties()

    def close(self) -> None:
        with self._send_lock:
            self._drop_producer()

    # ----- notebook 07 (starts a job and returns at once)
    def start_notebook(self, params: dict | None = None) -> str:
        self.connect()
        nb = self.items.get("Notebook")
        if not nb:
            raise FabricError(f"The workspace has no notebook named {self.settings.notebook_name}.")
        body = {"executionData": {"parameters": {k: _notebook_param(v) for k, v in params.items()}}} if params else None
        resp = self.client.request("POST", f"/v1/workspaces/{self.workspace_id}/items/{nb.id}/jobs/instances?jobType=RunNotebook", json=body)
        return resp.headers.get("Location", "")

    def job_status(self, location: str) -> dict:
        return self.client.request("GET", location).json()

    def recent_jobs(self, top: int = 5) -> list[dict]:
        self.connect()
        nb = self.items.get("Notebook")
        if not nb:
            return []
        body = self.client.request("GET", f"/v1/workspaces/{self.workspace_id}/items/{nb.id}/jobs/instances").json()
        return sorted(body.get("value", []), key=lambda j: j.get("startTimeUtc") or "", reverse=True)[:top]


def _token_account(token: str) -> str:
    import base64
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:  # noqa: BLE001
        return ""
    return claims.get("upn") or claims.get("unique_name") or claims.get("preferred_username") or ""
