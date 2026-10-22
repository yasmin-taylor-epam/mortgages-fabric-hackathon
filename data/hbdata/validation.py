"""Validate source exports and build clean account histories (notebook 01_validate_histories).

Bad records go to quarantine. They stay visible there and never reach clean
history or model inputs (T02). Feed watermarks flag stale feeds (T03).
Reversals keep their audit trail (T04).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

import pandas as pd

from . import contracts, io

SOURCE_DATASETS = ["mortgage_account", "account_party", "payment", "transfer_request_event",
                   "servicing_case_event", "servicing_contact", "observed_outcome"]
EVENT_DATASETS = ["payment", "transfer_request_event", "servicing_case_event", "servicing_contact"]
STALE_THRESHOLD_HOURS = {"mortgage_account": 24 * 730, "account_party": 24 * 730, "payment": 72,
                         "transfer_request_event": 24, "servicing_case_event": 72, "servicing_contact": 72,
                         "observed_outcome": 24 * 730}  # account, party and outcome rows change rarely


def _key(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    if df.empty:
        return pd.Series([], dtype=str)
    parts = [df[c].astype(str) for c in cols]
    out = parts[0]
    for p in parts[1:]:
        out = out + "|" + p
    return out


def _quarantine(dataset: str, rows: pd.DataFrame, reason: str, detail: str | pd.Series, detected_at) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame(columns=contracts.columns("quarantine_record"))
    keys = _key(rows, contracts.primary_key(dataset))
    recs = contracts.to_records(rows, dataset)
    details = detail if isinstance(detail, pd.Series) else pd.Series([detail] * len(rows), index=rows.index)
    qids = [f"QRN-{hashlib.sha1(f'{dataset}|{k}|{reason}|{json.dumps(r, sort_keys=True)}'.encode()).hexdigest()[:16]}"
            for k, r in zip(keys, recs)]
    return pd.DataFrame({
        "quarantine_id": qids, "dataset": dataset, "record_key": keys.values,
        "mortgage_id": rows["mortgage_id"].values if "mortgage_id" in rows else None,
        "reason": reason, "detail": details.values, "detected_at": detected_at,
        "record_json": [json.dumps(r, sort_keys=True) for r in recs],
    })


def schema_violations(df: pd.DataFrame, name: str) -> pd.Series:
    """Vectorised check of required, nullability, enum and pattern rules. Returns a detail per bad row."""
    schema = contracts.load_schema(name)
    detail = pd.Series("", index=df.index, dtype=object)
    for col, prop in schema["properties"].items():
        if col not in df.columns:
            detail[:] = detail + f"missing column {col}; "
            continue
        s = df[col]
        nullable = isinstance(prop.get("type"), list) and "null" in prop["type"]
        isnull = s.isna() if s.dtype != object else s.map(lambda v: v is None or (isinstance(v, float) and v != v))
        if col in schema.get("required", []) and not nullable:
            detail[isnull] += f"{col} is null; "
        present = ~isnull
        if "enum" in prop:
            allowed = {v for v in prop["enum"] if v is not None}
            bad = present & ~s.isin(allowed)
            detail[bad] += f"{col} not in enum; "
        if "pattern" in prop and present.any():
            bad = present & ~s.astype("str").str.match(prop["pattern"], na=True)
            detail[bad] += f"{col} fails pattern; "
    return detail[detail != ""]


def build_watermarks(frames: dict[str, pd.DataFrame], checked_at) -> pd.DataFrame:
    rows = []
    checked_at = pd.Timestamp(checked_at)
    for name, df in frames.items():
        schema = contracts.load_schema(name)
        ing, ev = schema.get("x-ingestion-time"), schema.get("x-event-time")
        max_ing = df[ing].max() if ing in df and len(df) else None
        max_ev = df[ev].max() if ev in df and len(df) else None
        thr = STALE_THRESHOLD_HOURS.get(name, 72)
        stale = max_ing is None or pd.isna(max_ing) or (checked_at - max_ing) > pd.Timedelta(hours=thr)
        rows.append(dict(dataset=name, checked_at=checked_at, max_ingested_at=max_ing, max_event_at=max_ev,
                         row_count=len(df), stale_threshold_hours=float(thr), is_stale=bool(stale)))
    return io.coerce(pd.DataFrame(rows), "feed_watermark")


def case_status_at(case_events: pd.DataFrame, cutoff=None, keys: tuple[str, ...] = ()) -> pd.DataFrame:
    """Reconstruct each case's latest state. With a cutoff, use only changes known at the cutoff (INV-2).

    keys adds grouping columns, for example snapshot_id when rows are already filtered per snapshot.
    """
    ev = case_events
    if cutoff is not None:
        ev = ev[(ev["changed_at"] <= cutoff) & (ev["recorded_at"] <= cutoff)]
    k = [*keys, "case_id"]
    if ev.empty:
        return pd.DataFrame(columns=[*k, "mortgage_id", "category", "support_route", "opened_at", "latest_status", "resolved_at"])
    ev = ev.sort_values([*k, "changed_at", "recorded_at"])
    g = ev.groupby(k, as_index=False, sort=False)
    first = g.first()[[*k, "mortgage_id", "category", "support_route", "changed_at"]]
    last = g.last()[[*k, "status", "changed_at"]]
    out = first.rename(columns={"changed_at": "opened_at"}).merge(
        last.rename(columns={"status": "latest_status", "changed_at": "last_changed_at"}), on=k)
    out["resolved_at"] = out["last_changed_at"].where(out["latest_status"] == "resolved")
    return out.drop(columns="last_changed_at")


def validate(exports: dict[str, pd.DataFrame], detected_at: datetime) -> dict:
    """Return clean tables, quarantine records, watermarks, report dimensions and a summary."""
    q: list[pd.DataFrame] = []
    clean: dict[str, pd.DataFrame] = {}
    summary: dict[str, dict] = {}
    detected_at = pd.Timestamp(detected_at)

    for name in SOURCE_DATASETS:
        df = exports[name].reset_index(drop=True)
        stats = {"input_rows": int(len(df))}
        bad = schema_violations(df, name)
        q.append(_quarantine(name, df.loc[bad.index], "schema_violation", bad, detected_at))
        df = df.drop(index=bad.index)
        pk = contracts.primary_key(name)
        key = _key(df, pk)
        dup_mask = key.duplicated(keep=False)
        if dup_mask.any():
            dups = df[dup_mask]
            as_json = pd.Series([json.dumps(r, sort_keys=True) for r in contracts.to_records(dups, name)], index=dups.index)
            variants = as_json.groupby(key[dup_mask]).nunique()
            conflict_keys = set(variants[variants > 1].index)
            conflict = dups[key[dup_mask].isin(conflict_keys)]
            q.append(_quarantine(name, conflict, "conflicting_duplicate", "same key with different content", detected_at))
            exact_drop = df.index[dup_mask & ~key.isin(conflict_keys) & key.duplicated(keep="first")]
            stats["exact_duplicates_dropped"] = int(len(exact_drop))
            df = df.drop(index=conflict.index.union(exact_drop))
        schema = contracts.load_schema(name)
        ev, ing = schema.get("x-event-time"), schema.get("x-ingestion-time")
        if name in EVENT_DATASETS:
            early = df[df[ing] < df[ev]]
            q.append(_quarantine(name, early, "ingested_before_occurred", f"{ing} before {ev}", detected_at))
            df = df.drop(index=early.index)
        clean[name] = df.reset_index(drop=True)
        summary[name] = stats

    known = set(clean["mortgage_account"]["mortgage_id"])
    for name in ["account_party", "payment", "transfer_request_event", "servicing_case_event", "servicing_contact", "observed_outcome"]:
        df = clean[name]
        unknown = df[~df["mortgage_id"].isin(known)]
        q.append(_quarantine(name, unknown, "unknown_account", "mortgage_id not in mortgage_account", detected_at))
        clean[name] = df.drop(index=unknown.index).reset_index(drop=True)

    # Conflicting borrower mapping: two approved primary borrowers with overlapping validity.
    party = clean["account_party"]
    prim = party[(party["role"] == "primary") & (party["mapping_status"] == "approved")]
    far = pd.Timestamp("2262-01-01", tz="UTC")
    pairs = prim.merge(prim, on="mortgage_id")
    pairs = pairs[(pairs["party_id_x"] < pairs["party_id_y"])
                  & (pairs["valid_from_x"] < pairs["valid_to_y"].fillna(far))
                  & (pairs["valid_from_y"] < pairs["valid_to_x"].fillna(far))]
    conflict_mortgages = set(pairs["mortgage_id"])
    bad = party[party["mortgage_id"].isin(conflict_mortgages)]
    q.append(_quarantine("account_party", bad, "conflicting_borrower_mapping", "overlapping approved primary borrowers", detected_at))
    clean["account_party"] = party.drop(index=bad.index).reset_index(drop=True)

    # Contacts must link to a known case of the same mortgage.
    case_owner = clean["servicing_case_event"].groupby("case_id")["mortgage_id"].first()
    con = clean["servicing_contact"]
    linked = con["case_id"].map(case_owner)
    unknown_case = con[linked.isna()]
    mismatch = con[linked.notna() & (linked != con["mortgage_id"])]
    q.append(_quarantine("servicing_contact", unknown_case, "unknown_case_link", "case_id not in servicing_case_event", detected_at))
    q.append(_quarantine("servicing_contact", mismatch, "conflicting_case_link", "case belongs to another mortgage", detected_at))
    clean["servicing_contact"] = con.drop(index=unknown_case.index.union(mismatch.index)).reset_index(drop=True)

    # Ledger reversals: target exists, same mortgage, not itself a reversal, exact negation, at most one reversal.
    pay = clean["payment"]
    rev = pay[pay["entry_type"] == "reversal"]
    target = rev[["entry_id", "mortgage_id", "reversal_of", "amount_received"]].merge(
        pay[["entry_id", "mortgage_id", "entry_type", "amount_received"]].rename(columns=lambda c: f"t_{c}"),
        left_on="reversal_of", right_on="t_entry_id", how="left")
    target.index = rev.index
    reason = pd.Series("", index=rev.index, dtype=object)
    reason[target["t_entry_id"].isna()] = "reversed entry not found"
    reason[(reason == "") & (target["t_mortgage_id"] != target["mortgage_id"])] = "reversed entry belongs to another mortgage"
    reason[(reason == "") & (target["t_entry_type"] == "reversal")] = "reversal of a reversal"
    reason[(reason == "") & ((target["amount_received"] + target["t_amount_received"]).abs() > 0.005)] = "amount does not negate the original"
    reason[(reason == "") & rev["reversal_of"].duplicated(keep="first")] = "entry already reversed"
    bad_rev = rev[reason != ""]
    q.append(_quarantine("payment", bad_rev, "invalid_reversal", reason[reason != ""], detected_at))
    clean["payment"] = pay.drop(index=bad_rev.index).reset_index(drop=True)

    quarantine = io.coerce(pd.concat([x for x in q if not x.empty], ignore_index=True) if any(not x.empty for x in q)
                           else pd.DataFrame(columns=contracts.columns("quarantine_record")), "quarantine_record")
    for name in SOURCE_DATASETS:
        clean[name] = io.coerce(clean[name], name)
        summary[name]["clean_rows"] = int(len(clean[name]))
        summary[name]["quarantined_rows"] = int((quarantine["dataset"] == name).sum())

    watermarks = build_watermarks(clean, detected_at)
    dims = build_report_dimensions(clean, conflict_mortgages, detected_at)
    return {"clean": clean, "quarantine": quarantine, "watermarks": watermarks, "dimensions": dims,
            "conflict_mortgages": sorted(conflict_mortgages), "summary": summary,
            "quarantine_by_reason": quarantine["reason"].value_counts().to_dict()}


def build_report_dimensions(clean: dict[str, pd.DataFrame], conflict_mortgages: set, as_of) -> dict[str, pd.DataFrame]:
    acct = clean["mortgage_account"].sort_values(["mortgage_id", "deal_start_at"])
    cur = acct.groupby("mortgage_id", as_index=False).last()
    mortgage = pd.DataFrame({
        "mortgage_id": cur["mortgage_id"], "current_cycle_id": cur["cycle_id"], "current_account_key": cur["account_key"],
        "product_type": cur["product_type"], "originated_at": cur["originated_at"], "loan_end_at": cur["loan_end_at"],
        "identity_status": ["conflict" if m in conflict_mortgages else "ok" for m in cur["mortgage_id"]],
    })
    status = case_status_at(clean["servicing_case_event"])
    contacts = clean["servicing_contact"].groupby("case_id")["contact_id"].nunique().rename("contact_count")
    case = status.merge(contacts, on="case_id", how="left")
    case["contact_count"] = case["contact_count"].fillna(0).astype(int)
    case["is_resolved"] = case["latest_status"] == "resolved"
    case["as_of_at"] = pd.Timestamp(as_of)
    return {"mortgage": io.coerce(mortgage, "mortgage"), "servicing_case": io.coerce(case, "servicing_case")}


def write_outputs(result: dict, lakehouse_dir) -> None:
    from pathlib import Path
    root = Path(lakehouse_dir)
    for name, df in result["clean"].items():
        io.write_table(df, root / "clean" / f"{name}.parquet", name)
    for name, df in result["dimensions"].items():
        io.write_table(df, root / "clean" / f"{name}.parquet", name)
    io.write_table(result["quarantine"], root / "quarantine" / "quarantine_record.parquet", "quarantine_record")
    io.write_table(result["watermarks"], root / "clean" / "feed_watermark.parquet", "feed_watermark")
    io.write_json({"summary": result["summary"], "quarantine_by_reason": result["quarantine_by_reason"],
                   "conflict_mortgages": result["conflict_mortgages"]}, root / "clean" / "_validation_report.json")
