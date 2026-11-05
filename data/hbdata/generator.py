"""Seeded synthetic data generator for the fictional mortgage lender HomeBridge.

Outcome labels come from hidden factors (rate shopping, broker contact,
engagement, financial stress and the portal friction the customer
experienced). They never come from an alert rule. All data is synthetic.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import __version__, io

UTC = timezone.utc

PRODUCTS = ["fixed_2y", "fixed_3y", "fixed_5y"]
DEAL_MONTHS = {"fixed_2y": 24, "fixed_3y": 36, "fixed_5y": 60}
BASE_RATE = {2019: 1.9, 2020: 1.8, 2021: 1.6, 2022: 3.3, 2023: 5.2, 2024: 4.6, 2025: 4.3}
FOLLOW_ON_RATE = 7.49  # illustrative follow-on rate, not an offer
STAGES = ["started", "product_selected", "documents_outstanding", "documents_received",
          "offer_in_review", "offer_issued", "offer_accepted", "completed"]
SYS_CODES = ["SYS-503", "SYS-500", "SYS-TIMEOUT"]
USR_CODES = ["USR-DOC-MISSING", "USR-DOC-UNREADABLE"]
EXCLUSIONS = [("sale_redemption", 0.04, "redemption_reason"),
              ("bereavement_redemption", 0.01, "redemption_reason"),
              ("redemption_unknown_reason", 0.03, "none")]

DATASETS = ["mortgage_account", "account_party", "payment", "transfer_request_event",
            "servicing_case_event", "servicing_contact", "observed_outcome"]


@dataclass(frozen=True)
class GenConfig:
    seed: int = 20261026
    as_of: datetime = datetime(2026, 10, 15, tzinfo=UTC)
    history_start: datetime = datetime(2025, 1, 1, tzinfo=UTC)
    labelled_cohorts: tuple[str, ...] = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06")
    per_cohort: int = 500
    open_cohort_size: int = 300
    open_deal_end_days: tuple[int, int] = (-25, 89)
    include_personas: bool = True
    include_anomalies: bool = True

    def to_json(self) -> dict:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        d["history_start"] = self.history_start.isoformat()
        return d


# ---------------------------------------------------------------- helpers

def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def add_months(dt: datetime, n: int) -> datetime:
    m = dt.month - 1 + n
    y = dt.year + m // 12
    m = m % 12 + 1
    d = min(dt.day, calendar.monthrange(y, m)[1])
    return dt.replace(year=y, month=m, day=d)


def months_between(a: datetime, b: datetime) -> int:
    return max(1, (b.year - a.year) * 12 + (b.month - a.month))


def days(x: float) -> timedelta:
    return timedelta(days=float(x))


def hours(x: float) -> timedelta:
    return timedelta(hours=float(x))


class Ids:
    def __init__(self) -> None:
        self.n: dict[str, int] = {}

    def next(self, prefix: str, width: int) -> str:
        self.n[prefix] = self.n.get(prefix, 0) + 1
        return f"{prefix}-{self.n[prefix]:0{width}d}"


@dataclass
class Sink:
    mortgage_account: list = field(default_factory=list)
    account_party: list = field(default_factory=list)
    payment: list = field(default_factory=list)
    transfer_request_event: list = field(default_factory=list)
    servicing_case_event: list = field(default_factory=list)
    servicing_contact: list = field(default_factory=list)
    observed_outcome: list = field(default_factory=list)


def lag_portal(rng) -> timedelta:
    return days(rng.uniform(2, 5)) if rng.random() < 0.03 else timedelta(seconds=float(rng.uniform(5, 900)))


def lag_case(rng) -> timedelta:
    return days(rng.uniform(2, 7)) if rng.random() < 0.04 else timedelta(minutes=float(rng.uniform(5, 240)))


def lag_contact(rng) -> timedelta:
    return days(rng.uniform(1, 4)) if rng.random() < 0.04 else timedelta(minutes=float(rng.uniform(1, 120)))


# ---------------------------------------------------------------- builders

def make_account(sink: Sink, ids: Ids, rng, mortgage_id: str, deal_end: datetime, *, product=None,
                 cycle_no=None, joint=None, owner=None) -> dict:
    product = product or str(rng.choice(PRODUCTS, p=[0.45, 0.15, 0.40]))
    deal_start = add_months(deal_end, -DEAL_MONTHS[product])
    cycle_no = cycle_no or int(rng.choice([1, 2, 3], p=[0.35, 0.40, 0.25]))
    prior = 0 if cycle_no == 1 else (cycle_no - 1) * int(rng.choice([24, 36, 60]))
    originated = add_months(deal_start, -prior)
    term_years = int(rng.integers(20, 36))
    loan_end = add_months(originated, 12 * term_years)
    base = BASE_RATE.get(deal_start.year, 4.5)
    fixed = round(float(np.clip(base + (0.15 if product == "fixed_2y" else 0.0) + rng.normal(0, 0.25), 0.9, 7.0)), 2)
    orig_balance = float(np.clip(rng.lognormal(math.log(190_000), 0.45), 40_000, 900_000))
    elapsed = (deal_start - originated).days / 365.25
    balance = round(orig_balance * max(0.05, 1 - elapsed / term_years) ** 0.8, 2)
    r = fixed / 1200
    n_left = months_between(deal_start, loan_end)
    payment = round(balance * r / (1 - (1 + r) ** -n_left), 2)
    owner = owner or f"ADV-{int(rng.integers(1, 41)):03d}"
    recorded = deal_start + hours(rng.integers(1, 48))
    row = dict(account_key=f"{mortgage_id}|C{cycle_no:02d}", mortgage_id=mortgage_id, cycle_id=f"C{cycle_no:02d}", cohort_id=deal_end.strftime("%Y-%m"),
               product_type=product, repayment_type="repayment", deal_start_at=deal_start, deal_end_at=deal_end,
               loan_end_at=loan_end, originated_at=originated, fixed_rate_pct=fixed, follow_on_rate_pct=FOLLOW_ON_RATE,
               balance=balance, balance_as_of=deal_start, current_payment=payment, owner_id=owner,
               recorded_at=recorded, ingested_at=recorded + hours(rng.integers(1, 24)))
    sink.mortgage_account.append(row)
    joint = bool(rng.random() < 0.45) if joint is None else joint
    for role in (["primary", "joint"] if joint else ["primary"]):
        sink.account_party.append(dict(mortgage_id=mortgage_id, party_id=ids.next("HB-P", 7), role=role,
                                       valid_from=originated, valid_to=None, mapping_status="approved",
                                       recorded_at=originated + days(1), ingested_at=originated + days(2)))
    return row


def add_transfer(sink: Sink, ids: Ids, rng, application_id: str, mortgage_id: str, ev: dict) -> None:
    sink.transfer_request_event.append(dict(
        event_id=ids.next("PTE", 9), application_id=application_id, mortgage_id=mortgage_id,
        occurred_at=ev["occurred_at"], ingested_at=ev.get("ingested_at") or ev["occurred_at"] + lag_portal(rng),
        stage=ev["stage"], status=ev["status"], error_code=ev.get("error_code"), error_class=ev.get("error_class"),
        fault_verified=ev.get("fault_verified"), channel=ev.get("channel", "portal")))


def walk_request(rng, start_at: datetime, deal_end: datetime, *, stress: float, shop: float,
                 fault_plan: dict | None, doc_error: bool, rejection: bool):
    """Simulate a request path up to offer acceptance. Returns (events, completion_time or None)."""
    ev: list[dict] = []
    t = start_at
    dwell = {"started": 0.4, "product_selected": 1.5, "documents_outstanding": 5 + 3 * max(stress, 0),
             "documents_received": 1.5, "offer_in_review": 4, "offer_issued": 3 + 4 * max(shop, 0)}

    def emit(stage, status, code=None, cls=None, verified=None):
        ev.append(dict(occurred_at=t, stage=stage, status=status, error_code=code, error_class=cls, fault_verified=verified))

    for stage in STAGES[:-1]:
        emit(stage, "in_progress")
        if fault_plan and fault_plan["stage"] == stage:
            for _ in range(fault_plan["n"]):
                t += timedelta(minutes=float(rng.uniform(1, 30)))
                emit(stage, "error", str(rng.choice(SYS_CODES)), "software_fault", bool(rng.random() < 0.85))
                t += hours(rng.uniform(0.2, 36))
                emit(stage, "in_progress")
        if doc_error and stage == "documents_outstanding":
            t += days(rng.uniform(0.5, 3))
            emit(stage, "error", str(rng.choice(USR_CODES)), "customer_input")
            t += days(rng.uniform(1, 5))
            emit(stage, "in_progress")
        if rejection and stage == "offer_in_review":
            t += days(rng.uniform(1, 4))
            emit(stage, "rejected", "ELG-ARREARS", "eligibility")
            return ev, None
        if stage in dwell:
            t += days(rng.exponential(dwell[stage]) + 0.05)
    completed = max(t + days(1), deal_end - days(int(rng.integers(0, 7))) + hours(9))
    return ev, completed


def max_stage_dwell(events: list[dict]) -> float:
    starts: list[tuple[str, datetime]] = []
    for e in events:
        if not starts or starts[-1][0] != e["stage"]:
            starts.append((e["stage"], e["occurred_at"]))
    gaps = [(b[1] - a[1]).total_seconds() / 86400 for a, b in zip(starts, starts[1:])]
    return max(gaps, default=0.0)


def make_case(sink: Sink, ids: Ids, rng, mortgage_id: str, owner: str, category: str, route: str,
              opened_at: datetime, resolved_at: datetime | None, n_contacts: int, *, end_at: datetime,
              contact_times: list[datetime] | None = None) -> str:
    case_id = ids.next("CASE", 7)
    states = [("opened", opened_at)]
    t_prog = opened_at + hours(rng.uniform(1, 30))
    if resolved_at is None or t_prog < resolved_at:
        states.append(("in_progress", t_prog))
    if resolved_at is not None:
        states.append(("resolved", resolved_at))
    for status, t in states:
        if t > end_at:
            continue
        sink.servicing_case_event.append(dict(case_event_id=ids.next("SCE", 9), case_id=case_id, mortgage_id=mortgage_id,
                                              changed_at=t, recorded_at=t + lag_case(rng), category=category,
                                              status=status, owner_id=owner, support_route=route))
    if contact_times is None:
        span_end = resolved_at or min(end_at, opened_at + days(30))
        span = max((span_end - opened_at).total_seconds(), 600)
        contact_times = [opened_at + timedelta(minutes=float(rng.uniform(1, 20)))]
        contact_times += sorted(opened_at + timedelta(seconds=float(rng.uniform(600, span))) for _ in range(n_contacts - 1))
    for t in contact_times:
        if t > end_at:
            continue
        sink.servicing_contact.append(dict(contact_id=ids.next("CON", 8), case_id=case_id, mortgage_id=mortgage_id,
                                           occurred_at=t, ingested_at=t + lag_contact(rng),
                                           channel=str(rng.choice(["phone", "email", "webchat", "secure_message"], p=[0.5, 0.2, 0.2, 0.1])),
                                           direction="inbound" if rng.random() < 0.85 else "outbound"))
    return case_id


def payment_ingest(rng, posted: datetime, outages: list[tuple[datetime, datetime]]) -> datetime:
    for s, e in outages:
        if s <= posted < e:
            return e + hours(rng.uniform(1, 6))
    return posted + hours(rng.uniform(2, 30))


def make_payments(sink: Sink, ids: Ids, rng, acct: dict, *, start: datetime, end_at: datetime,
                  onset: datetime | None, outages, clean: bool = False) -> list[dict]:
    """Monthly collections from start to end_at. Returns the scheduled-collection rows."""
    due_day = int(rng.integers(1, 29))
    y, m = start.year, start.month
    amount = acct["current_payment"]
    rows = []
    while True:
        due = datetime(y, m, due_day, tzinfo=UTC)
        m += 1
        if m > 12:
            y, m = y + 1, 1
        if due > end_at:
            break
        if due < start:
            continue
        stressed = onset is not None and due >= onset
        posted = due + hours(rng.integers(6, 60))
        fail_p = 0.0 if clean else (0.4 if stressed else 0.008)
        failed = rng.random() < fail_p
        entry = dict(entry_id=ids.next("PAY", 9), mortgage_id=acct["mortgage_id"], entry_type="scheduled_collection",
                     due_at=due, posted_at=posted, ingested_at=payment_ingest(rng, posted, outages),
                     amount_due=amount, amount_received=0.0 if failed else amount, reversal_of=None,
                     reason_code=str(rng.choice(["insufficient_funds", "mandate_cancelled", "technical_failure"], p=[0.8, 0.1, 0.1])) if failed else None)
        sink.payment.append(entry)
        rows.append(entry)
        if failed:
            if rng.random() < 0.55:
                add_manual_payment(sink, ids, rng, entry, posted + days(rng.integers(3, 20)), outages)
            continue
        rev_p = 0.0 if clean else (0.04 if stressed else 0.004)
        if rng.random() < rev_p:
            rev = add_reversal(sink, ids, rng, entry, posted + days(rng.integers(2, 10)), outages)
            if rng.random() < 0.6:
                add_manual_payment(sink, ids, rng, entry, rev["posted_at"] + days(rng.integers(2, 12)), outages)
    return rows


def add_reversal(sink, ids, rng, entry, posted, outages, reason=None) -> dict:
    rev = dict(entry_id=ids.next("PAY", 9), mortgage_id=entry["mortgage_id"], entry_type="reversal", due_at=entry["due_at"],
               posted_at=posted, ingested_at=payment_ingest(rng, posted, outages), amount_due=0.0,
               amount_received=-entry["amount_received"], reversal_of=entry["entry_id"],
               reason_code=reason or str(rng.choice(["indemnity_claim", "bank_recall"])))
    sink.payment.append(rev)
    return rev


def add_manual_payment(sink, ids, rng, entry, posted, outages) -> dict:
    row = dict(entry_id=ids.next("PAY", 9), mortgage_id=entry["mortgage_id"], entry_type="manual_payment", due_at=entry["due_at"],
               posted_at=posted, ingested_at=payment_ingest(rng, posted, outages), amount_due=0.0,
               amount_received=entry["amount_due"], reversal_of=None, reason_code=None)
    sink.payment.append(row)
    return row


def outcome_row(cfg: GenConfig, acct: dict, outcome_type: str, outcome_at, observed_at, evidence: str) -> dict:
    deal_end = acct["deal_end_at"]
    window_end = deal_end + days(30)
    row = dict(mortgage_id=acct["mortgage_id"], cycle_id=acct["cycle_id"], checkpoint_at=deal_end - days(90),
               observation_window_end_at=window_end, outcome_type=outcome_type, outcome_at=outcome_at,
               outcome_observed_at=observed_at, evidence_source=evidence)
    # INV-4: a label exists only when the window has ended and the outcome is observed and in scope.
    if window_end > cfg.as_of or observed_at is None or observed_at > cfg.as_of:
        row.update(outcome_type="not_yet_observed", outcome_at=None, outcome_observed_at=None,
                   evidence_source="none", label_status="pending", label=None)
    elif outcome_type in ("external_refinance", "product_transfer", "continued_servicing"):
        row.update(label_status="labelled", label=1 if outcome_type == "external_refinance" else 0)
    else:
        row.update(label_status="excluded", label=None)
    return row


# ---------------------------------------------------------------- one bulk mortgage

def bulk_mortgage(cfg: GenConfig, sink: Sink, ids: Ids, rng, mortgage_id: str, deal_end: datetime, outages) -> None:
    acct = make_account(sink, ids, rng, mortgage_id, deal_end)
    owner = acct["owner_id"]
    checkpoint = deal_end - days(90)
    shop, stress, engage = (float(x) for x in rng.normal(size=3))
    broker = rng.random() < sigmoid(-1.4 + 0.9 * shop)
    fault_exposed = rng.random() < 0.18
    onset = None
    if stress > 1.35:
        onset = cfg.history_start + days(rng.uniform(60, (cfg.as_of - cfg.history_start).days))

    # Product-transfer request (full path before the outcome decision).
    started = rng.random() < sigmoid(1.3 + 0.8 * engage - 0.3 * shop)
    req_events: list[dict] = []
    completed_at = None
    rejected = False
    if started:
        offset = float(np.clip(rng.normal(100 + 25 * engage + 20 * shop, 28), 12, 175))
        start_at = deal_end - days(offset) + hours(rng.uniform(7, 21))
        fault_plan = {"stage": str(rng.choice(["product_selected", "documents_received", "offer_accepted"])),
                      "n": int(rng.integers(1, 5))} if fault_exposed else None
        rejected = rng.random() < (0.03 + (0.25 if onset is not None and onset < start_at else 0.0))
        req_events, completed_at = walk_request(rng, start_at, deal_end, stress=stress, shop=shop, fault_plan=fault_plan,
                                                doc_error=rng.random() < 0.18, rejection=rejected)
    sw_errors = [e for e in req_events if e["error_class"] == "software_fault" and e["occurred_at"] < deal_end]
    redemption_at = None
    end_at = cfg.as_of

    # Servicing cases. Contacts link to cases.
    span_days = (cfg.as_of - cfg.history_start).days
    for _ in range(int(rng.poisson(0.9 * span_days / 365))):
        opened = cfg.history_start + days(rng.uniform(0, span_days))
        cat = str(rng.choice(["general_enquiry", "billing_enquiry", "rate_enquiry"], p=[0.45, 0.35, 0.20]))
        make_case(sink, ids, rng, mortgage_id, owner, cat, "general_servicing", opened, opened + days(rng.uniform(0.2, 6)),
                  int(rng.choice([1, 2, 3], p=[0.7, 0.25, 0.05])), end_at=end_at)
    if rng.random() < 0.7 * sigmoid(2.5 * shop - 0.5):
        opened = deal_end - days(rng.uniform(60, 170))
        make_case(sink, ids, rng, mortgage_id, owner, "rate_enquiry", "general_servicing", opened,
                  opened + days(rng.uniform(1, 3)), int(rng.integers(1, 3)), end_at=end_at)
    unresolved_complaint = False
    if sw_errors and rng.random() < 0.5:
        opened = sw_errors[0]["occurred_at"] + days(rng.uniform(0, 3))
        resolved = opened + days(3 + rng.exponential(20))
        unresolved_complaint = resolved > checkpoint and opened <= checkpoint + days(30)
        make_case(sink, ids, rng, mortgage_id, owner, "complaint", "complaints_team", opened,
                  resolved if resolved <= cfg.as_of else None, int(rng.integers(2, 5)), end_at=end_at)
    if sw_errors and rng.random() < 0.6:
        opened = sw_errors[0]["occurred_at"] + hours(rng.uniform(1, 48))
        make_case(sink, ids, rng, mortgage_id, owner, "portal_problem", "technical_support", opened,
                  opened + days(rng.uniform(1, 10)), 1, end_at=end_at)
    if rng.random() < 0.04:
        opened = cfg.history_start + days(rng.uniform(0, span_days))
        resolved = opened + days(2 + rng.exponential(15))
        make_case(sink, ids, rng, mortgage_id, owner, "complaint", "complaints_team", opened,
                  resolved if resolved <= cfg.as_of else None, int(rng.integers(2, 4)), end_at=end_at)
    if onset is not None and rng.random() < 0.7:
        opened = onset + days(rng.uniform(30, 70))
        if opened <= cfg.as_of:
            resolved = opened + days(rng.uniform(30, 120))
            make_case(sink, ids, rng, mortgage_id, owner, "payment_difficulty", "payment_support", opened,
                      resolved if resolved <= cfg.as_of else None, int(rng.integers(1, 4)), end_at=end_at)

    # Outcome from hidden factors and experienced friction. Never from an alert rule.
    window_end = deal_end + days(30)
    u = rng.random()
    acc = 0.0
    excluded = None
    for name, p, evidence in EXCLUSIONS:
        acc += p
        if u < acc:
            excluded = (name, evidence)
            break
    logit = (-2.3 + 0.95 * shop + 0.35 * (acct["follow_on_rate_pct"] - acct["fixed_rate_pct"] - 3.8) + 0.6 * broker + 0.35 * min(len(sw_errors), 4) + 0.7 * unresolved_complaint
             + 0.03 * min(max_stage_dwell(req_events), 30) - 0.55 * engage - 0.5 * max(stress - 1, 0)
             + (0.3 if not started else 0.0) + rng.normal(0, 0.6))
    if excluded:
        outcome_type, evidence = excluded
        redemption_at = deal_end + days(rng.integers(-80, 25))
        outcome_at, observed_at = redemption_at, redemption_at + days(rng.integers(2, 10))
    elif rng.random() < sigmoid(logit):
        outcome_type, evidence = "external_refinance", "refinance_record"
        redemption_at = max(checkpoint + days(1), deal_end + days(rng.integers(-21, 26)))
        outcome_at, observed_at = redemption_at, redemption_at + days(rng.integers(2, 10))
    elif started and not rejected and rng.random() > 0.08:
        outcome_type, evidence = "product_transfer", "transfer_completion"
        outcome_at, observed_at = completed_at, completed_at + hours(rng.uniform(6, 48))
    else:
        outcome_type, evidence = "continued_servicing", "servicing_continued"
        outcome_at, observed_at = deal_end, window_end

    # Cut the request path to match the outcome.
    if req_events:
        if outcome_type == "product_transfer":
            req_events.append(dict(occurred_at=completed_at, stage="completed", status="completed",
                                   error_code=None, error_class=None, fault_verified=None))
        elif not rejected:
            first = req_events[0]["occurred_at"]
            latest = (redemption_at or deal_end) - days(5)
            if latest > first + days(1):
                cut = first + days(rng.uniform(1, (latest - first).total_seconds() / 86400))
                req_events = [e for e in req_events if e["occurred_at"] < cut]
                if rng.random() < 0.55:
                    req_events.append(dict(occurred_at=cut, stage=req_events[-1]["stage"], status="abandoned",
                                           error_code=None, error_class=None, fault_verified=None))
        app_id = ids.next("PTR", 7)
        for e in req_events:
            if redemption_at is None or e["occurred_at"] <= redemption_at:
                add_transfer(sink, ids, rng, app_id, mortgage_id, e)

    pay_end = min(cfg.as_of, redemption_at) if redemption_at else cfg.as_of
    make_payments(sink, ids, rng, acct, start=max(cfg.history_start, acct["deal_start_at"]), end_at=pay_end,
                  onset=onset, outages=outages)
    sink.observed_outcome.append(outcome_row(cfg, acct, outcome_type, outcome_at, observed_at, evidence))


# ---------------------------------------------------------------- anomalies (T02 and ledger checks)

def inject_anomalies(sink: Sink, ids: Ids, rng) -> dict:
    """Add a small, known set of bad records so validation has something to catch."""
    counts: dict[str, int] = {}
    def bulk(rows):
        return [r for r in rows if not r["mortgage_id"].startswith("HB-M-9000")]
    pays = bulk(sink.payment)
    for i in range(8):
        src = pays[int(rng.integers(0, len(pays)))]
        sink.payment.append({**src, "entry_id": ids.next("PAY", 9), "mortgage_id": f"HB-M-99{i:04d}",
                             "entry_type": "scheduled_collection", "reversal_of": None, "amount_received": abs(src["amount_received"])})
    counts["unknown_account"] = 8
    evs = bulk(sink.transfer_request_event)
    for i in range(5):
        src = evs[int(rng.integers(0, len(evs)))]
        sink.transfer_request_event.append({**src, "event_id": ids.next("PTE", 9), "mortgage_id": f"HB-M-98{i:04d}"})
    counts["unknown_account"] += 5
    cons = bulk(sink.servicing_contact)
    for i in range(4):
        src = cons[int(rng.integers(0, len(cons)))]
        sink.servicing_contact.append({**src, "contact_id": ids.next("CON", 8), "case_id": f"CASE-99{i:05d}"})
    counts["unknown_case_link"] = 4
    accounts = [a["mortgage_id"] for a in bulk(sink.mortgage_account)]
    for i in range(3):
        src = cons[int(rng.integers(0, len(cons)))]
        other = next(m for m in accounts[int(rng.integers(0, len(accounts))):] + accounts if m != src["mortgage_id"])
        sink.servicing_contact.append({**src, "contact_id": ids.next("CON", 8), "mortgage_id": other})
    counts["conflicting_case_link"] = 3
    for i in range(2):
        primary = [p for p in bulk(sink.account_party) if p["role"] == "primary"][int(rng.integers(0, len(accounts)))]
        sink.account_party.append({**primary, "party_id": ids.next("HB-P", 7), "valid_from": primary["valid_from"] + days(30)})
    counts["conflicting_borrower_mapping"] = 2  # mortgages affected
    for i in range(15):
        src = evs[int(rng.integers(0, len(evs)))]
        sink.transfer_request_event.append(dict(src))
    counts["exact_duplicate"] = 15
    for i in range(2):
        src = evs[int(rng.integers(0, len(evs)))]
        sink.transfer_request_event.append({**src, "status": "abandoned", "error_code": None, "error_class": None, "fault_verified": None})
    counts["conflicting_duplicate"] = 2  # keys affected
    for i in range(3):
        src = next(p for p in pays[int(rng.integers(0, len(pays))):] if p["entry_type"] == "scheduled_collection" and p["amount_received"] > 0)
        sink.payment.append({**src, "entry_id": ids.next("PAY", 9), "entry_type": "reversal", "amount_due": 0.0,
                             "amount_received": -src["amount_received"], "reversal_of": f"PAY-9{i:08d}",
                             "reason_code": "bank_recall", "posted_at": src["posted_at"] + days(3), "ingested_at": src["ingested_at"] + days(3)})
    counts["invalid_reversal"] = 3
    for i in range(2):
        src = cons[int(rng.integers(0, len(cons)))]
        sink.servicing_contact.append({**src, "contact_id": ids.next("CON", 8), "ingested_at": src["occurred_at"] - hours(5)})
    counts["ingested_before_occurred"] = 2
    return counts


# ---------------------------------------------------------------- main entry

def make_outages(cfg: GenConfig, rng) -> list[tuple[datetime, datetime]]:
    span = (cfg.as_of - cfg.history_start).days - 60
    starts = sorted(cfg.history_start + days(int(rng.integers(60, span))) for _ in range(5))
    return [(s, s + days(int(rng.integers(4, 7)))) for s in starts]


def _apply_as_of(cfg: GenConfig, frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Drop rows that had not happened or not arrived by the simulated as-of time."""
    rules = {"payment": ("posted_at", "ingested_at"), "transfer_request_event": ("occurred_at", "ingested_at"),
             "servicing_case_event": ("changed_at", "recorded_at"), "servicing_contact": ("occurred_at", "ingested_at")}
    for name, (ev, ing) in rules.items():
        df = frames[name]
        frames[name] = df[(df[ev] <= cfg.as_of) & (df[ing] <= cfg.as_of)].reset_index(drop=True)
    return frames


def generate(cfg: GenConfig = GenConfig()) -> tuple[dict[str, pd.DataFrame], dict]:
    from . import personas

    root = np.random.default_rng(cfg.seed)
    outages = make_outages(cfg, root)
    sink, ids = Sink(), Ids()
    n = 0
    for cohort in cfg.labelled_cohorts:
        y, m = (int(x) for x in cohort.split("-"))
        dim = calendar.monthrange(y, m)[1]
        for _ in range(cfg.per_cohort):
            n += 1
            rng = np.random.default_rng([cfg.seed, n])
            deal_end = datetime(y, m, 1, tzinfo=UTC) + days(int(rng.integers(0, dim)))
            bulk_mortgage(cfg, sink, ids, rng, f"HB-M-{n:06d}", deal_end, outages)
    lo, hi = cfg.open_deal_end_days
    base = cfg.as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(cfg.open_cohort_size):
        n += 1
        rng = np.random.default_rng([cfg.seed, n])
        bulk_mortgage(cfg, sink, ids, rng, f"HB-M-{n:06d}", base + days(int(rng.integers(lo, hi + 1))), outages)
    persona_ids = personas.add_personas(cfg, sink, ids, outages) if cfg.include_personas else {}
    anomalies = inject_anomalies(sink, ids, np.random.default_rng([cfg.seed, 999_999])) if cfg.include_anomalies else {}

    frames = {name: io.coerce(pd.DataFrame(getattr(sink, name)), name) for name in DATASETS}
    frames = _apply_as_of(cfg, frames)
    out = frames["observed_outcome"]
    manifest = {
        "generator": "hbdata.generator", "generator_version": __version__, "config": cfg.to_json(),
        "fictional_company": "HomeBridge", "synthetic": True,
        "row_counts": {k: int(len(v)) for k, v in frames.items()},
        "payment_feed_outages": [[s.isoformat(), e.isoformat()] for s, e in outages],
        "injected_anomalies": anomalies, "personas": persona_ids,
        "outcomes_by_cohort": {c: g["outcome_type"].value_counts().to_dict() for c, g in
                               out.merge(frames["mortgage_account"][["mortgage_id", "cycle_id", "cohort_id"]]).groupby("cohort_id")},
        "label_rate_by_cohort": {c: round(float(g["label"].mean()), 4) for c, g in
                                 out[out["label_status"] == "labelled"].merge(frames["mortgage_account"][["mortgage_id", "cycle_id", "cohort_id"]]).groupby("cohort_id")},
    }
    return frames, manifest


def write_exports(frames: dict[str, pd.DataFrame], manifest: dict, out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    hashes = {}
    for name, df in frames.items():
        path = io.write_table(df, out_dir / f"{name}.parquet", name)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    manifest = {**manifest, "file_sha256_16": hashes}
    io.write_json(manifest, out_dir / "manifest.json")
    return manifest
