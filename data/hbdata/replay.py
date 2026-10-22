"""Live-event replay and the local Eventhouse stand-in.

Eventstream replays renewal-portal and servicing events into Eventhouse
(blueprint step 5). Each event keeps its original event_id and occurred_at.
Locally, "Eventhouse" is a folder of Parquet files:

- raw_events.parquet: accepted events. Duplicates stay; queries deduplicate (T01).
- quarantined_events.parquet: unknown accounts, identity conflicts and schema failures (T02).

The functions here are twins of kql/functions/*.kql.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from . import contracts, io

RAW_COLS = ["event_id", "event_type", "schema_version", "source_system", "mortgage_id", "application_id",
            "occurred_at", "sent_at", "payload", "ingested_at"]
QUAR_COLS = ["event_id", "mortgage_id", "reason", "detail", "detected_at", "envelope"]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(rows: list[dict], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    return path


def _iso(ts) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def transfer_envelopes(events: pd.DataFrame, keep_ingestion: bool = True) -> list[dict]:
    """Wrap transfer_request_event rows as live envelopes (for a history backfill or a fixture)."""
    out = []
    for r in events.to_dict("records"):
        out.append({
            "event_id": r["event_id"], "event_type": "transfer_request_event", "schema_version": "1.0",
            "source_system": "renewal_portal", "mortgage_id": r["mortgage_id"], "application_id": r["application_id"],
            "occurred_at": _iso(r["occurred_at"]), "sent_at": _iso(r["occurred_at"]),
            "simulated_ingested_at": _iso(r["ingested_at"]) if keep_ingestion else None,
            "payload": {"stage": r["stage"], "status": r["status"],
                        "error_code": None if pd.isna(r["error_code"]) else r["error_code"],
                        "error_class": None if pd.isna(r["error_class"]) else r["error_class"],
                        "fault_verified": None if pd.isna(r["fault_verified"]) else bool(r["fault_verified"]),
                        "channel": r["channel"]},
        })
    return out


def read_local(eventhouse_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = Path(eventhouse_dir)
    raw = io.read_table(d / "raw_events.parquet") if (d / "raw_events.parquet").exists() else pd.DataFrame(columns=RAW_COLS)
    q = io.read_table(d / "quarantined_events.parquet") if (d / "quarantined_events.parquet").exists() else pd.DataFrame(columns=QUAR_COLS)
    return raw, q


def ingest_local(envelopes: list[dict], eventhouse_dir: Path, context: pd.DataFrame, now) -> dict:
    """Append envelopes to the local Eventhouse. Twin of the update policies in kql/tables.kql."""
    now = pd.Timestamp(now)
    known = context[context["identity_status"] == "ok"]["mortgage_id"]
    conflict = set(context[context["identity_status"] != "ok"]["mortgage_id"])
    known = set(known)
    acc, bad = [], []
    for env in envelopes:
        errs = contracts.validate_records([env], "live_event", 5)
        ing = pd.Timestamp(env["simulated_ingested_at"]) if env.get("simulated_ingested_at") else now
        if errs:
            bad.append(dict(event_id=env.get("event_id"), mortgage_id=env.get("mortgage_id"), reason="schema_violation",
                            detail="; ".join(errs), detected_at=ing, envelope=json.dumps(env, sort_keys=True)))
        elif env["mortgage_id"] in conflict:
            bad.append(dict(event_id=env["event_id"], mortgage_id=env["mortgage_id"], reason="conflicting_borrower_mapping",
                            detail="identity_status is conflict", detected_at=ing, envelope=json.dumps(env, sort_keys=True)))
        elif env["mortgage_id"] not in known:
            bad.append(dict(event_id=env["event_id"], mortgage_id=env["mortgage_id"], reason="unknown_account",
                            detail="mortgage_id not in AccountContext", detected_at=ing, envelope=json.dumps(env, sort_keys=True)))
        else:
            acc.append(dict(event_id=env["event_id"], event_type=env["event_type"], schema_version=env["schema_version"],
                            source_system=env["source_system"], mortgage_id=env["mortgage_id"],
                            application_id=env.get("application_id"), occurred_at=pd.Timestamp(env["occurred_at"]),
                            sent_at=pd.Timestamp(env["sent_at"]), payload=json.dumps(env["payload"], sort_keys=True),
                            ingested_at=ing))
    raw, q = read_local(eventhouse_dir)
    d = Path(eventhouse_dir)
    d.mkdir(parents=True, exist_ok=True)
    if acc:
        raw = pd.concat([raw, pd.DataFrame(acc)], ignore_index=True) if len(raw) else pd.DataFrame(acc)
        for c in ("occurred_at", "sent_at", "ingested_at"):
            raw[c] = pd.to_datetime(raw[c], utc=True)
        raw[RAW_COLS].to_parquet(d / "raw_events.parquet", index=False)
    if bad:
        q = pd.concat([q, pd.DataFrame(bad)], ignore_index=True) if len(q) else pd.DataFrame(bad)
        q["detected_at"] = pd.to_datetime(q["detected_at"], utc=True)
        q[QUAR_COLS].to_parquet(d / "quarantined_events.parquet", index=False)
    return {"accepted": len(acc), "quarantined": len(bad), "quarantine_reasons": pd.Series([b["reason"] for b in bad]).value_counts().to_dict()}


def transfer_events(raw: pd.DataFrame) -> pd.DataFrame:
    """Twin of TransferEvents(): accepted transfer_request_event rows as the transfer_request_event table shape."""
    r = raw[raw["event_type"] == "transfer_request_event"]
    cols = contracts.columns("transfer_request_event")
    if r.empty:
        return pd.DataFrame(columns=cols)
    p = pd.DataFrame([json.loads(x) for x in r["payload"]], index=r.index)
    out = pd.concat([r[["event_id", "application_id", "mortgage_id", "occurred_at", "ingested_at"]], p], axis=1)
    return out[cols].reset_index(drop=True)


def map_cycle(df: pd.DataFrame, context: pd.DataFrame) -> pd.DataFrame:
    """Map each event to the deal cycle that covers its event time (deal start to deal end plus 30 days)."""
    m = df.merge(context[["mortgage_id", "cycle_id", "deal_start_at", "deal_end_at"]], on="mortgage_id")
    m = m[(m["occurred_at"] >= m["deal_start_at"]) & (m["occurred_at"] < m["deal_end_at"] + pd.Timedelta(days=30))]
    return m.sort_values("deal_start_at").drop_duplicates("event_id", keep="last").drop(columns=["deal_start_at", "deal_end_at"])


def service_events(raw: pd.DataFrame, context: pd.DataFrame, cutoff=None) -> pd.DataFrame:
    """Twin of ServiceEvents(): derive the Dashboard service-event shape from accepted live events."""
    cols = contracts.columns("service_event")
    if raw.empty:
        return pd.DataFrame(columns=cols)
    r = raw
    if cutoff is not None:
        r = r[(r["occurred_at"] <= cutoff) & (r["ingested_at"] <= cutoff)]
    r = r.sort_values(["event_id", "ingested_at"]).drop_duplicates("event_id", keep="first")
    rows = []
    for e in r.to_dict("records"):
        p = json.loads(e["payload"])
        et, ev_ids = None, [e["event_id"]]
        if e["event_type"] == "transfer_request_event":
            if p.get("error_class") == "software_fault" and p.get("fault_verified") is True:
                et, ev_ids = "portal_fault_verified", [e["event_id"], e["application_id"]]
        elif e["event_type"] in ("payment_support_requested", "payment_support_closed"):
            et, ev_ids = e["event_type"], [e["event_id"], p["case_id"]]
        elif e["event_type"] == "payment_entry_posted" and p.get("entry_type") == "reversal":
            et, ev_ids = "payment_reversed", [e["event_id"], p["reversal_of"]]
        if et:
            rows.append(dict(event_id=e["event_id"], event_type=et, mortgage_id=e["mortgage_id"], occurred_at=e["occurred_at"],
                             ingested_at=e["ingested_at"], evidence_ids=ev_ids))
    if not rows:
        return pd.DataFrame(columns=cols)
    out = map_cycle(pd.DataFrame(rows), context)
    return io.coerce(out, "service_event")


def send_eventstream(envelopes: list[dict]) -> int:
    """Send envelopes to a Fabric Eventstream custom endpoint (Event Hub protocol).

    Reads the connection string from HB_EVENTSTREAM_CONNECTION and the hub name from
    HB_EVENTSTREAM_NAME. Never store either value in the repository. Needs azure-eventhub.
    """
    conn = os.environ.get("HB_EVENTSTREAM_CONNECTION")
    name = os.environ.get("HB_EVENTSTREAM_NAME")
    if not conn or not name:
        raise RuntimeError("Set HB_EVENTSTREAM_CONNECTION and HB_EVENTSTREAM_NAME to send to Eventstream.")
    from azure.eventhub import EventData, EventHubProducerClient  # optional dependency

    sendable = [{k: v for k, v in e.items() if k != "simulated_ingested_at"} for e in envelopes]
    with EventHubProducerClient.from_connection_string(conn, eventhub_name=name) as producer:
        batch = producer.create_batch()
        for env in sendable:
            batch.add(EventData(json.dumps(env)))
        producer.send_batch(batch)
    return len(sendable)
