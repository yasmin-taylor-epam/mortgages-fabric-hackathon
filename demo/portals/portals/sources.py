"""Where the portals get their data.

LiveSource reads Fabric: the Lakehouse SQL analytics endpoint (T-SQL) and the Eventhouse (KQL). It keeps each
answer for a short time (cache_seconds for the Eventhouse, sql_cache_seconds for the Lakehouse). A send forgets
only the Eventhouse answers. A finished routing job forgets everything.
It sends events to the Eventstream and starts notebook 07 as a Fabric job.

OfflineSource is for rehearsal and tests without Fabric. It reads the local synthetic files in data/output with
DuckDB, using the same SQL. Sent events go to a temporary copy of the local Eventhouse stand-in through
hbdata.replay.ingest_local (the twin of the KQL update policies). "Process new events" runs the same routing
module as notebook 07 on the laptop. Every offline page carries an "Offline preview" banner.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from .config import REPO, Settings

MORTGAGE_ID = re.compile(r"^HB-M-\d{6}$")
EVENT_ID = re.compile(r"^[A-Z]+-[A-Za-z0-9-]{4,40}$")
LIVE_RANGE = "97"  # portal events use IDs like PTE-97xxxxxxx and PSE-97xxxxxxx
LIVE_KQL = "(event_id startswith 'PTE-97' or event_id startswith 'PSE-97')"
SQL_SETTLING_SECONDS = 180


def is_live(event_id: str) -> bool:
    return str(event_id)[4:6] == LIVE_RANGE and str(event_id)[:4] in ("PTE-", "PSE-")


def check_mortgage_id(mid: str) -> str:
    if not MORTGAGE_ID.match(mid or ""):
        raise ValueError(f"'{mid}' is not a mortgage ID like HB-M-900001.")
    return mid


def check_event_id(eid: str) -> str:
    if not EVENT_ID.match(eid or ""):
        raise ValueError(f"'{eid}' is not an event ID.")
    return eid


class Source(Protocol):
    kind: str  # "fabric" or "offline"

    def sql(self, query: str, params: tuple = ()) -> list[dict]: ...
    def raw_events(self, mortgage_id: str) -> list[dict]: ...
    def event_arrival(self, event_id: str) -> dict: ...
    def event_counts(self) -> dict: ...
    def send_event(self, envelope: dict) -> None: ...
    def start_routing(self) -> str: ...
    def routing_status(self, ref: str) -> dict: ...
    def links(self) -> dict: ...


def _rows(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso")) if len(df) else []


SENT_LOG = Path(__file__).resolve().parents[1] / "sent-times.local.json"


class SentLog:
    """The real time this laptop sent each portal event. It turns Fabric's real arrival time into a delay in seconds,
    so the pages can show arrivals on the demo clock. Git ignores the file. It holds event IDs and times only."""

    def __init__(self, path: Path | None = SENT_LOG):
        self.path = path
        self._lock = threading.Lock()
        self.times: dict[str, str] = {}
        if path and path.exists():
            try:
                self.times = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                self.times = {}

    def record(self, event_id: str) -> None:
        with self._lock:
            self.times.setdefault(event_id, datetime.now(timezone.utc).isoformat())
            if self.path:
                try:
                    self.path.write_text(json.dumps(self.times, indent=1), encoding="utf-8")
                except OSError:
                    pass  # the delay is shown for this run only

    def get(self, event_id: str) -> str | None:
        return self.times.get(event_id)


def arrival_delay(sent_at_real: str | None, ingested_at) -> int | None:
    """Seconds from the click to the first copy in RawEvents. None when this laptop did not send the event."""
    if not sent_at_real or not ingested_at:
        return None
    sent, ing = pd.Timestamp(sent_at_real), pd.Timestamp(ingested_at)
    sent = sent.tz_localize("UTC") if sent.tzinfo is None else sent
    ing = ing.tz_localize("UTC") if ing.tzinfo is None else ing
    return max(0, round((ing - sent).total_seconds()))


def _with_delay(found: dict, sent_log: SentLog, event_id: str) -> dict:
    first = found["accepted"][0]["ingested_at"] if found["accepted"] else None
    found["delay_seconds"] = arrival_delay(sent_log.get(event_id), first)
    return found


# ---------------------------------------------------------------- live Fabric

class LiveSource:
    kind = "fabric"

    def __init__(self, settings: Settings, fabric=None, sent_log: SentLog | None = None):
        from .fabric import Fabric
        self.settings = settings
        self.fabric = fabric or Fabric(settings)
        self.sent_log = sent_log if sent_log is not None else SentLog()
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._cleared_jobs: set[str] = set()
        self._sql_settling_until = 0.0

    def clear_cache(self, kind: str | None = None) -> None:
        """Forget cached reads. kind 'kql' is the Eventhouse, 'sql' is the Lakehouse, None is both."""
        with self._lock:
            for key in [k for k in self._cache if kind is None or k[0] == kind]:
                del self._cache[key]

    def _cached(self, key: tuple, fn):
        now = time.monotonic()
        # the Lakehouse SQL endpoint shows new Delta rows about a minute late, so re-read it often after a routing job
        long_sql = key[0] == "sql" and now >= self._sql_settling_until
        ttl = self.settings.sql_cache_seconds if long_sql else self.settings.cache_seconds
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = fn()
        with self._lock:
            self._cache[key] = (now, value)
        return value

    def warm_up(self) -> None:
        """Sign in, open the SQL connection and the Eventstream sender, and fill the cache for the personas."""
        from .board import Board
        self.fabric.warm_up()
        b = Board(self.settings, self)
        b.worklist()
        for p in self.settings.personas:
            b.portal_home(p.mortgage_id)

    def sql(self, query: str, params: tuple = ()) -> list[dict]:
        return self._cached(("sql", query, params), lambda: self.fabric.sql(query, params))

    def kql(self, query: str) -> list[dict]:
        return self._cached(("kql", query), lambda: self.fabric.kql(query))

    def raw_events(self, mortgage_id: str) -> list[dict]:
        mid = check_mortgage_id(mortgage_id)
        return self.kql(f"RawEvents | where mortgage_id == '{mid}' | summarize copies = count(), arg_min(ingested_at, *) by event_id"
                        " | top 40 by occurred_at desc"
                        " | project event_id, event_type, source_system, application_id, occurred_at, ingested_at, payload, copies")

    def event_arrival(self, event_id: str) -> dict:
        eid = check_event_id(event_id)
        accepted = self.fabric.kql(f"RawEvents | where event_id == '{eid}' | project event_id, ingested_at | order by ingested_at asc")
        quarantined = self.fabric.kql(f"QuarantinedEvents | where event_id == '{eid}' | project event_id, reason, detected_at")
        service = self.fabric.kql(f"ServiceEvents() | where event_id == '{eid}' | project event_id, event_type, cycle_id")
        if accepted or quarantined:
            self.clear_cache("kql")  # the event is in: the next page shows it at once
        return _with_delay({"accepted": accepted, "quarantined": quarantined, "service": service}, self.sent_log, eid)

    def event_counts(self) -> dict:
        rows = self.kql(
            "print raw = toscalar(RawEvents | count), quarantined = toscalar(QuarantinedEvents | count),"
            f" live = toscalar(RawEvents | where {LIVE_KQL} | count),"
            f" last_live_occurred = toscalar(RawEvents | where {LIVE_KQL} | summarize max(occurred_at)),"
            " last_ingested = toscalar(RawEvents | summarize max(ingested_at)),"
            " service = toscalar(ServiceEvents() | count)")
        return rows[0] if rows else {}

    def send_event(self, envelope: dict) -> None:
        self.fabric.send_event(envelope)
        self.sent_log.record(envelope["event_id"])
        self.clear_cache("kql")  # the Lakehouse does not change until the routing notebook runs

    def start_routing(self) -> str:
        f = self.fabric.connect()
        return f.start_notebook({"KUSTO_URI": f.kusto_uri, "KUSTO_DB": f.kusto_db})

    def routing_status(self, ref: str) -> dict:
        body = self.fabric.job_status(ref) if ref else {}
        if body.get("status") in {"Completed", "Failed", "Cancelled"} and ref not in self._cleared_jobs:
            self._cleared_jobs.add(ref)
            self._sql_settling_until = time.monotonic() + SQL_SETTLING_SECONDS
            self.clear_cache()  # notebook 07 wrote new review cases
        return body

    def recent_routing(self) -> list[dict]:
        return self.fabric.recent_jobs()

    def links(self) -> dict:
        f = self.fabric.connect()
        return {"workspace": f.workspace_link(), "lakehouse": f.link("Lakehouse"), "kql": f.link("KQLDatabase"),
                "eventhouse": f.link("Eventhouse"), "eventstream": f.link("Eventstream"), "notebook": f.link("Notebook"),
                "report": f.link("Report"), "pipeline": f.link("DataPipeline")}


# ---------------------------------------------------------------- offline preview

LAKEHOUSE_TABLES = {
    "mortgage": "clean/mortgage.parquet",
    "mortgage_account": "clean/mortgage_account.parquet",
    "servicing_case": "clean/servicing_case.parquet",
    "servicing_contact": "clean/servicing_contact.parquet",
    "transfer_request_event": "clean/transfer_request_event.parquet",
    "account_scores": "gold/account_scores.parquet",
    "feature_attributions": "gold/feature_attributions.parquet",
    "segment_membership": "gold/segment_membership.parquet",
    "score_runs": "gold/score_runs.parquet",
}


class OfflineSource:
    kind = "offline"

    def __init__(self, settings: Settings, output_dir: Path | None = None, route_at_start: bool = True):
        import duckdb
        for p in (REPO / "data", REPO / "action", REPO / "notebooks"):
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
        self.settings = settings
        self.output = output_dir or REPO / "data" / "output"
        if not (self.output / "eventhouse" / "account_context.parquet").exists():
            raise SystemExit("Offline preview needs data/output. Run scripts/run_local_flow.ps1 first.")
        self.tmp = Path(tempfile.mkdtemp(prefix="hb-portals-"))
        shutil.copytree(self.output / "eventhouse", self.tmp / "eventhouse")
        self.db = duckdb.connect()
        self._lock = threading.Lock()
        for table, rel in LAKEHOUSE_TABLES.items():
            path = (self.output / "lakehouse" / rel).as_posix()
            self.db.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
        self.context = pd.read_parquet(self.tmp / "eventhouse" / "account_context.parquet")
        self.routing_runs: list[dict] = []
        self.sent_log = SentLog(None)  # offline: kept in memory only
        self._register("review_routing_runs", pd.DataFrame(columns=["routed_at", "score_run_id", "service_events", "cases",
                                                                    "rejected_events", "exceptions", "summary"]))
        self._register("review_cases", pd.DataFrame(columns=["case_key", "account_key", "mortgage_id", "cycle_id", "action_family",
                                                             "status", "assigned_to", "case_created_at"]))
        self._register("review_decisions", pd.DataFrame(columns=["event_id", "mortgage_id", "outcome", "reason"]))
        if route_at_start:
            self.start_routing()

    def _register(self, name: str, df: pd.DataFrame) -> None:
        with self._lock:
            self.db.register(name, df)

    def clear_cache(self, kind: str | None = None) -> None:
        pass

    def warm_up(self) -> None:
        pass

    def sql(self, query: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            df = self.db.execute(query, list(params)).df()
        return _rows(df)

    def _raw(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        from hbdata import replay
        return replay.read_local(self.tmp / "eventhouse")

    def raw_events(self, mortgage_id: str) -> list[dict]:
        mid = check_mortgage_id(mortgage_id)
        raw, _ = self._raw()
        df = raw[raw["mortgage_id"] == mid].copy()
        if df.empty:
            return []
        df["copies"] = df.groupby("event_id")["event_id"].transform("size")
        df = df.sort_values("ingested_at").drop_duplicates("event_id").sort_values("occurred_at", ascending=False).head(40)
        return _rows(df[["event_id", "event_type", "source_system", "application_id", "occurred_at", "ingested_at", "payload", "copies"]])

    def event_arrival(self, event_id: str) -> dict:
        from hbdata import replay
        eid = check_event_id(event_id)
        raw, q = self._raw()
        service = replay.service_events(raw[raw["event_id"] == eid], self.context)
        return _with_delay({"accepted": _rows(raw.loc[raw["event_id"] == eid, ["event_id", "ingested_at"]].sort_values("ingested_at")),
                            "quarantined": _rows(q.loc[q["event_id"] == eid, ["event_id", "reason", "detected_at"]]),
                            "service": _rows(service[["event_id", "event_type", "cycle_id"]]) if len(service) else []},
                           self.sent_log, eid)

    def event_counts(self) -> dict:
        from hbdata import replay
        raw, q = self._raw()
        live = raw[raw["event_id"].astype(str).map(is_live)]
        return {"raw": len(raw), "quarantined": len(q), "live": len(live),
                "last_live_occurred": live["occurred_at"].max().isoformat() if len(live) else None,
                "last_ingested": raw["ingested_at"].max().isoformat() if len(raw) else None,
                "service": len(replay.service_events(raw, self.context))}

    def send_event(self, envelope: dict) -> None:
        from hbdata import replay
        self.sent_log.record(envelope["event_id"])
        replay.ingest_local([envelope], self.tmp / "eventhouse", self.context, datetime.now(timezone.utc))

    def start_routing(self) -> str:
        from hbdata import replay
        from homebridge_action.models import RoutingPolicy
        from review_routing import route as rr
        routed_at = datetime.now(timezone.utc)
        policy = RoutingPolicy.from_dict(json.loads(self.settings.policy_path.read_text(encoding="utf-8")))
        gold = self.output / "lakehouse" / "gold"
        score_runs = pd.read_parquet(gold / "score_runs.parquet")
        run_id = rr.latest_completed_run(score_runs)
        risk = rr.risk_candidates(pd.read_parquet(gold / "risk_outbox.parquet"), run_id)
        raw, _ = self._raw()
        service = rr.service_candidates(_rows(replay.service_events(raw, self.context)))
        result = rr.route(rr.contexts(self.context), risk + service, dict(zip(score_runs["run_id"], score_runs["status"])),
                          policy, routed_at)
        summary = rr.summary(result, run_id, len(service), routed_at)
        self.routing_runs.append({"routed_at": routed_at, "score_run_id": run_id, "service_events": len(service),
                                  "cases": len(result["cases"]), "rejected_events": len(result["rejected"]),
                                  "exceptions": len(result["exceptions"]), "summary": json.dumps(summary)})
        self._register("review_cases", result["cases"])
        self._register("review_decisions", result["decisions"])
        self._register("review_routing_runs", pd.DataFrame(self.routing_runs))
        return f"offline-{len(self.routing_runs)}"

    def routing_status(self, ref: str) -> dict:
        return {"status": "Completed", "id": ref, "note": "Offline preview: routing ran on this laptop."}

    def recent_routing(self) -> list[dict]:
        return [{"id": f"offline-{i + 1}", "status": "Completed", "startTimeUtc": r["routed_at"].isoformat()}
                for i, r in enumerate(self.routing_runs)][::-1]

    def links(self) -> dict:
        return {}

    def close(self) -> None:
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
