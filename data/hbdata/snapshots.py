"""Frozen feature snapshots (notebook 02_feature_snapshots).

INV-2: every input row has its event time and its ingestion time at or before
cutoff_at. Observed outcomes never enter a snapshot. A snapshot is immutable
per snapshot_id: rewriting one with different content raises an error.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import contracts, io, journeys
from .validation import case_status_at

FEATURE_VERSION = "fv1"
CHECKPOINT_DAYS = 90
PAYMENT_STALE_HOURS = 72
SOURCES = ["mortgage_account", "account_party", "payment", "transfer_request_event", "servicing_case_event", "servicing_contact"]
D = pd.Timedelta(days=1)


def snapshot_id(mortgage_id: str, cycle_id: str, cutoff_at, feature_version: str = FEATURE_VERSION) -> str:
    ts = pd.Timestamp(cutoff_at).tz_convert("UTC").isoformat()
    return "SNP-" + hashlib.sha1(f"{mortgage_id}|{cycle_id}|{ts}|{feature_version}".encode()).hexdigest()[:16]


def checkpoint_requests(accounts: pd.DataFrame, as_of) -> pd.DataFrame:
    """One checkpoint per deal cycle: deal_end_at minus 90 days, if it is not after as_of."""
    r = accounts[["mortgage_id", "cycle_id", "deal_end_at"]].copy()
    r["cutoff_at"] = r["deal_end_at"] - CHECKPOINT_DAYS * D
    r = r[r["cutoff_at"] <= pd.Timestamp(as_of)]
    r["snapshot_kind"] = "checkpoint"
    return r[["mortgage_id", "cycle_id", "cutoff_at", "snapshot_kind"]].reset_index(drop=True)


def live_requests(accounts: pd.DataFrame, cutoff_at, mortgage_ids=None) -> pd.DataFrame:
    """Live snapshots at one cutoff for open deal cycles (deal end not yet reached plus 30 days)."""
    cutoff_at = pd.Timestamp(cutoff_at)
    a = accounts[(accounts["deal_start_at"] <= cutoff_at) & (accounts["deal_end_at"] + 30 * D >= cutoff_at)]
    if mortgage_ids is not None:
        a = a[a["mortgage_id"].isin(set(mortgage_ids))]
    r = a[["mortgage_id", "cycle_id"]].copy()
    r["cutoff_at"] = cutoff_at
    r["snapshot_kind"] = "live"
    return r.reset_index(drop=True)


def _watermark_lookup(series: pd.Series, cutoffs: pd.Series) -> pd.Series:
    """Latest value of series at or before each cutoff. NaT when none exists."""
    out = pd.Series(pd.NaT, index=cutoffs.index, dtype="datetime64[us, UTC]")
    s = pd.to_datetime(series.dropna(), utc=True).sort_values()
    if s.empty or cutoffs.empty:
        return out
    arr = s.dt.tz_convert(None).to_numpy(dtype="datetime64[us]")
    cut = pd.to_datetime(cutoffs, utc=True).dt.tz_convert(None).to_numpy(dtype="datetime64[us]")
    idx = np.searchsorted(arr, cut, side="right") - 1
    vals = pd.Series(pd.DatetimeIndex(arr[np.clip(idx, 0, None)]).tz_localize("UTC"), index=cutoffs.index)
    return vals.where(idx >= 0, out)


def _per_snapshot(df: pd.DataFrame, req: pd.DataFrame, ev: str, ing: str) -> pd.DataFrame:
    m = df.merge(req[["snapshot_id", "mortgage_id", "cutoff_at", "deal_start_at"]], on="mortgage_id")
    return m[(m[ev] <= m["cutoff_at"]) & (m[ing] <= m["cutoff_at"])]


def _transfer_features(tr: pd.DataFrame, req: pd.DataFrame) -> pd.DataFrame:
    tr = tr[tr["occurred_at"] >= tr["deal_start_at"]]  # map requests to this deal cycle
    tr = journeys.known_at(tr, None, ("snapshot_id",))
    out = pd.DataFrame({"snapshot_id": req["snapshot_id"]})
    if tr.empty:
        return out
    t = journeys.flagged_events(tr, ("snapshot_id", "application_id"))
    age = (t["cutoff_at"] - t["occurred_at"]) / D
    t["in30"], t["in90"] = age <= 30, age <= 90
    t["failed"] = t["status"].isin(["error", "rejected"])
    t["cust_err"] = (t["error_class"] == "customer_input").fillna(False).astype(bool)
    for name, flag, window in (("pt_failed_attempts_30d", "failed", "in30"), ("pt_failed_attempts_90d", "failed", "in90"),
                               ("pt_software_errors_90d", "is_software_fault", "in90"),
                               ("pt_retries_after_error_90d", "is_retry", "in90"),
                               ("pt_customer_input_errors_90d", "cust_err", "in90")):
        t[name] = (t[flag].astype(bool) & t[window]).astype(int)
    apps = t.groupby(["snapshot_id", "application_id"], as_index=False).agg(
        first_at=("occurred_at", "min"), last_stage=("stage", "last"), last_status=("status", "last"),
        completed=("status", lambda s: bool((s == "completed").any())), cutoff_at=("cutoff_at", "first"))
    latest = apps.sort_values(["snapshot_id", "first_at"]).groupby("snapshot_id").last()
    agg = t.groupby("snapshot_id").agg(
        pt_request_count=("application_id", "nunique"),
        pt_failed_attempts_30d=("pt_failed_attempts_30d", "sum"), pt_failed_attempts_90d=("pt_failed_attempts_90d", "sum"),
        pt_software_errors_90d=("pt_software_errors_90d", "sum"), pt_retries_after_error_90d=("pt_retries_after_error_90d", "sum"),
        pt_customer_input_errors_90d=("pt_customer_input_errors_90d", "sum"))
    dwell = journeys.stage_dwell(tr, None, ("snapshot_id", "application_id"))
    dw_max = dwell.groupby("snapshot_id")["dwell_days"].max().rename("pt_max_stage_dwell_days")
    open_v = dwell[dwell["is_open"]].merge(latest[["application_id"]].reset_index(), on=["snapshot_id", "application_id"])
    dw_cur = open_v.groupby("snapshot_id")["dwell_days"].max().rename("pt_days_at_current_stage")
    rt = journeys.retries_after_error(tr, None, ("snapshot_id", "application_id"))
    unrec = rt.assign(u=~rt["recovered"]).groupby("snapshot_id")["u"].any().rename("pt_unrecovered_software_error")
    ab = apps.assign(x=apps["last_status"].isin(["abandoned", "rejected"])).groupby("snapshot_id")["x"].sum().rename("pt_abandoned_or_rejected_count")
    ev_ids = t[t["is_software_fault"]].groupby("snapshot_id")["event_id"].apply(lambda s: list(s)[-5:]).rename("pt_evidence")
    lat = pd.DataFrame({
        "pt_latest_stage": latest["last_stage"], "pt_latest_status": latest["last_status"],
        "pt_completed": latest["completed"], "pt_days_since_start": (latest["cutoff_at"] - latest["first_at"]) / D,
        "pt_latest_application": latest["application_id"]})
    for part in (agg, dw_max, dw_cur, unrec, ab, ev_ids, lat):
        out = out.merge(part, left_on="snapshot_id", right_index=True, how="left")
    return out


def _servicing_features(cases: pd.DataFrame, contacts: pd.DataFrame, req: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"snapshot_id": req["snapshot_id"]})
    cutoff = req.set_index("snapshot_id")["cutoff_at"]
    sensitive: set = set()
    if not cases.empty:
        st = case_status_at(cases, None, ("snapshot_id",))  # rows are already filtered to each cutoff
        # Category comes only from case events known at the cutoff (INV-2).
        sensitive = set(zip(st.loc[st["category"] == "payment_difficulty", "snapshot_id"], st.loc[st["category"] == "payment_difficulty", "case_id"]))
        st["cutoff_at"] = st["snapshot_id"].map(cutoff)
        st["unresolved"] = st["latest_status"] != "resolved"
        st["age_days"] = (st["cutoff_at"] - st["opened_at"]) / D
        st["in365"] = st["age_days"] <= 365
        pay = st[st["category"] == "payment_difficulty"]
        support = pay[pay["unresolved"]].groupby("snapshot_id").size().gt(0).rename("ctx_payment_support_open")
        st = st[st["category"] != "payment_difficulty"]  # sensitive support details stay out of model features
        g = None
        cnt = lambda cat: st[st["in365"] & (st["category"] == cat)].groupby("snapshot_id").size()  # noqa: E731
        f = pd.DataFrame({
            "svc_cases_opened_365d": st[st["in365"]].groupby("snapshot_id").size(),
            "svc_open_cases_at_cutoff": st[st["unresolved"]].groupby("snapshot_id").size(),
            "svc_unresolved_complaint": st[st["unresolved"] & (st["category"] == "complaint")].groupby("snapshot_id").size().gt(0),
            "svc_oldest_unresolved_complaint_age_days": st[st["unresolved"] & (st["category"] == "complaint")].groupby("snapshot_id")["age_days"].max(),
            "svc_cases_general_365d": cnt("general_enquiry"), "svc_cases_billing_365d": cnt("billing_enquiry"),
            "svc_cases_rate_365d": cnt("rate_enquiry"), "svc_cases_complaint_365d": cnt("complaint"),
            "svc_cases_portal_365d": cnt("portal_problem"),
            "svc_evidence": st[st["unresolved"]].groupby("snapshot_id")["case_id"].apply(list),
        })
        out = out.merge(f, left_on="snapshot_id", right_index=True, how="left").merge(support, left_on="snapshot_id", right_index=True, how="left")
        unresolved_keys = set(zip(st.loc[st["unresolved"], "snapshot_id"], st.loc[st["unresolved"], "case_id"]))
    else:
        unresolved_keys = set()
    if not contacts.empty:
        c = contacts[[k not in sensitive for k in zip(contacts["snapshot_id"], contacts["case_id"])]].drop_duplicates(["snapshot_id", "contact_id"])
        age = (c["cutoff_at"] - c["occurred_at"]) / D
        c30 = c[age <= 30].groupby("snapshot_id").size()
        c90 = c[age <= 90].groupby("snapshot_id").size()
        per_case = c[age <= 365].groupby(["snapshot_id", "case_id"]).size()
        unres = per_case[[k in unresolved_keys for k in per_case.index]]
        f = pd.DataFrame({
            "svc_contacts_30d": c30, "svc_contacts_90d": c90,
            "svc_max_contacts_per_case_365d": per_case.groupby(level=0).max(),
            "svc_repeat_contacts_unresolved": (unres - 1).clip(lower=0).groupby(level=0).sum(),
        })
        out = out.merge(f, left_on="snapshot_id", right_index=True, how="left")
    return out


def _payment_features(pay: pd.DataFrame, req: pd.DataFrame, all_payments: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"snapshot_id": req["snapshot_id"]})
    wm = _watermark_lookup(all_payments["ingested_at"], req["cutoff_at"])
    out["ctx_payment_feed_watermark"] = wm.set_axis(out.index)
    out["ctx_payment_feed_stale"] = (wm.isna() | ((req["cutoff_at"] - wm) > pd.Timedelta(hours=PAYMENT_STALE_HOURS))).set_axis(out.index)
    if not pay.empty:
        p = pay[(pay["cutoff_at"] - pay["due_at"]) / D <= 180]
        per_due = p.groupby(["snapshot_id", "due_at"]).agg(due=("amount_due", "sum"), recv=("amount_received", "sum"))
        per_due["short"] = (per_due["due"] - per_due["recv"]).clip(lower=0)
        f = pd.DataFrame({
            "ctx_missed_payments_180d": (per_due["short"] > 0.005).groupby(level=0).sum(),
            "ctx_arrears_amount": per_due["short"].groupby(level=0).sum().round(2),
            "ctx_reversals_180d": p[p["entry_type"] == "reversal"].groupby("snapshot_id").size(),
        })
        out = out.merge(f, left_on="snapshot_id", right_index=True, how="left")
    for c in ("ctx_missed_payments_180d", "ctx_reversals_180d"):
        out[c] = (out[c] if c in out else pd.Series(0, index=out.index)).fillna(0).astype("Int64")
    out["ctx_arrears_amount"] = (out["ctx_arrears_amount"] if "ctx_arrears_amount" in out else pd.Series(0.0, index=out.index)).fillna(0.0).astype(float)
    stale = out["ctx_payment_feed_stale"]
    out.loc[stale, ["ctx_missed_payments_180d", "ctx_reversals_180d"]] = pd.NA  # T03: missing feed is not zero arrears
    out.loc[stale, "ctx_arrears_amount"] = np.nan
    return out


def build_snapshots(clean: dict[str, pd.DataFrame], requests: pd.DataFrame, created_at,
                    extra_transfer_events: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build one frozen snapshot per request row (mortgage_id, cycle_id, cutoff_at, snapshot_kind)."""
    acct = clean["mortgage_account"]
    req = requests.merge(acct, on=["mortgage_id", "cycle_id"], how="inner")
    req = req[req["ingested_at"] <= req["cutoff_at"]].reset_index(drop=True)  # terms must be known at the cutoff
    req["snapshot_id"] = [snapshot_id(m, c, t) for m, c, t in zip(req["mortgage_id"], req["cycle_id"], req["cutoff_at"])]
    req = req.drop_duplicates("snapshot_id").reset_index(drop=True)

    base = pd.DataFrame({
        "snapshot_id": req["snapshot_id"], "mortgage_id": req["mortgage_id"], "cycle_id": req["cycle_id"],
        "cohort_id": req["cohort_id"], "cutoff_at": req["cutoff_at"], "snapshot_kind": req["snapshot_kind"],
        "feature_version": FEATURE_VERSION, "created_at": pd.Timestamp(created_at),
        "days_to_deal_end": (req["deal_end_at"] - req["cutoff_at"]) / D,
        "product_type": req["product_type"], "fixed_rate_pct": req["fixed_rate_pct"],
        "follow_on_rate_pct": req["follow_on_rate_pct"], "rate_gap_pct": (req["follow_on_rate_pct"] - req["fixed_rate_pct"]).round(2),
        "balance": req["balance"], "current_payment": req["current_payment"],
        "deal_length_months": req["product_type"].map({"fixed_2y": 24, "fixed_3y": 36, "fixed_5y": 60}),
        "months_since_origination": ((req["cutoff_at"] - req["originated_at"]) / D / 30.4375).round(1),
        "years_to_loan_end": ((req["loan_end_at"] - req["cutoff_at"]) / D / 365.25).round(2),
    })

    party = _per_snapshot(clean["account_party"], req, "valid_from", "ingested_at")
    party = party[party["valid_to"].isna() | (party["valid_to"] > party["cutoff_at"])]
    party = party[party["mapping_status"] == "approved"]
    base["borrower_count"] = base["snapshot_id"].map(party.groupby("snapshot_id")["party_id"].nunique()).fillna(0).astype(int)

    transfers = clean["transfer_request_event"]
    if extra_transfer_events is not None and len(extra_transfer_events):
        transfers = pd.concat([transfers, extra_transfer_events[transfers.columns]], ignore_index=True)
    tr = _per_snapshot(transfers, req, "occurred_at", "ingested_at")
    cases = _per_snapshot(clean["servicing_case_event"], req, "changed_at", "recorded_at")
    contacts = _per_snapshot(clean["servicing_contact"], req, "occurred_at", "ingested_at")
    pay = _per_snapshot(clean["payment"], req, "posted_at", "ingested_at")

    feats = (base.merge(_transfer_features(tr, req), on="snapshot_id", how="left")
             .merge(_servicing_features(cases, contacts, req), on="snapshot_id", how="left")
             .merge(_payment_features(pay, req, clean["payment"]), on="snapshot_id", how="left"))

    ints = ["pt_request_count", "pt_failed_attempts_30d", "pt_failed_attempts_90d", "pt_software_errors_90d",
            "pt_retries_after_error_90d", "pt_customer_input_errors_90d", "pt_abandoned_or_rejected_count",
            "svc_cases_opened_365d", "svc_open_cases_at_cutoff", "svc_cases_general_365d", "svc_cases_billing_365d",
            "svc_cases_rate_365d", "svc_cases_complaint_365d", "svc_cases_portal_365d", "svc_contacts_30d",
            "svc_contacts_90d", "svc_max_contacts_per_case_365d", "svc_repeat_contacts_unresolved"]
    for c in ints:
        feats[c] = (feats[c] if c in feats else pd.Series(0, index=feats.index)).fillna(0).astype(int)
    for c in ("pt_completed", "pt_unrecovered_software_error", "svc_unresolved_complaint", "ctx_payment_support_open"):
        feats[c] = (feats[c] if c in feats else pd.Series(False, index=feats.index)).fillna(False).astype(bool)
    for c in ("pt_latest_stage", "pt_latest_status", "pt_days_since_start", "pt_days_at_current_stage",
              "pt_max_stage_dwell_days", "svc_oldest_unresolved_complaint_age_days"):
        if c not in feats:
            feats[c] = None
    feats["pt_request_started"] = feats["pt_request_count"] > 0
    feats["pt_failed_ratio_30_90"] = (feats["pt_failed_attempts_30d"] / feats["pt_failed_attempts_90d"].where(feats["pt_failed_attempts_90d"] > 0)).round(4)
    feats["svc_contact_trend"] = (feats["svc_contacts_30d"] - (feats["svc_contacts_90d"] - feats["svc_contacts_30d"]) / 2).astype(float)

    def evidence(row) -> list[str]:
        ids = []
        if isinstance(row.get("pt_latest_application"), str):
            ids.append(row["pt_latest_application"])
        for col in ("pt_evidence", "svc_evidence"):
            if isinstance(row.get(col), list):
                ids.extend(row[col])
        return ids

    feats["evidence_ids"] = [evidence(r) for r in feats[["pt_latest_application", "pt_evidence", "svc_evidence"]].to_dict("records")] \
        if "pt_evidence" in feats and "svc_evidence" in feats else [[] for _ in range(len(feats))]

    wm_cols = {}
    for name in SOURCES:
        ing = contracts.load_schema(name)["x-ingestion-time"]
        wm_cols[name] = _watermark_lookup(clean[name][ing], feats["cutoff_at"])
    feats["source_watermark"] = [json.dumps({k: (None if pd.isna(v[i]) else v[i].isoformat()) for k, v in wm_cols.items()})
                                 for i in range(len(feats))]
    return io.coerce(feats, "feature_snapshot")


def _content(df: pd.DataFrame) -> pd.Series:
    cols = [c for c in df.columns if c != "created_at"]
    recs = contracts.to_records(df[cols], "feature_snapshot")
    return pd.Series([json.dumps(r, sort_keys=True) for r in recs], index=df["snapshot_id"].values)


def append_snapshots(new: pd.DataFrame, path: Path) -> dict:
    """Append new snapshots. Existing snapshot_ids must keep identical content (immutability)."""
    path = Path(path)
    if path.exists():
        old = io.read_table(path)
        both = set(old["snapshot_id"]) & set(new["snapshot_id"])
        if both:
            a = _content(old[old["snapshot_id"].isin(both)])
            b = _content(new[new["snapshot_id"].isin(both)])
            changed = [k for k in both if a[k] != b[k]]
            if changed:
                raise ValueError(f"{len(changed)} existing snapshots would change, for example {changed[:3]}. Snapshots are immutable.")
        add = new[~new["snapshot_id"].isin(set(old["snapshot_id"]))]
        combined = pd.concat([old, add], ignore_index=True)
    else:
        add, combined = new, new
    io.write_table(combined, path, "feature_snapshot")
    return {"existing": int(len(combined) - len(add)), "added": int(len(add)), "total": int(len(combined))}
