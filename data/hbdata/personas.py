"""Scripted, illustrative personas for the demo. All are fictional.

- Alex: portal faults during a product-transfer request, a payment concern and an
  unresolved complaint. Open cohort. Checkpoint 5 days before as_of.
- Sam: completed product transfer with no payment problems, plus one reversed
  collection that was repaid. Labelled cohort 2026-03.
- Casey (T07): three contacts about one unresolved complaint.
- Robin (T07): three contacts about three separate, resolved enquiries.
"""
from __future__ import annotations

import numpy as np

from .generator import (Ids, Sink, GenConfig, add_manual_payment, add_reversal, add_transfer, days, hours,
                        make_account, make_case, make_payments, outcome_row)

ALEX, SAM, CASEY, ROBIN = "HB-M-900001", "HB-M-900002", "HB-M-900003", "HB-M-900004"


def _request(sink: Sink, ids: Ids, rng, mortgage_id: str, steps: list[tuple]) -> str:
    app_id = ids.next("PTR", 7)
    for t, stage, status, *rest in steps:
        code, cls, verified = (rest + [None, None, None])[:3]
        add_transfer(sink, ids, rng, app_id, mortgage_id,
                     dict(occurred_at=t, ingested_at=t + hours(0.05), stage=stage, status=status,
                          error_code=code, error_class=cls, fault_verified=verified))
    return app_id


def add_personas(cfg: GenConfig, sink: Sink, ids: Ids, outages) -> dict:
    base = cfg.as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    out = {}

    # Alex
    rng = np.random.default_rng([cfg.seed, 900001])
    deal_end = base + days(85)
    acct = make_account(sink, ids, rng, ALEX, deal_end, product="fixed_5y", cycle_no=2, joint=False, owner="ADV-007")
    rows = make_payments(sink, ids, rng, acct, start=cfg.history_start, end_at=cfg.as_of, onset=None, outages=[], clean=True)
    missed = min(rows, key=lambda r: abs((r["due_at"] - (base - days(40))).days))
    missed.update(amount_received=0.0, reason_code="insufficient_funds")
    add_manual_payment(sink, ids, rng, missed, missed["posted_at"] + days(9), [])
    s = base - days(30) + hours(19)
    app = _request(sink, ids, rng, ALEX, [
        (s, "started", "in_progress"), (s + hours(0.3), "product_selected", "in_progress"),
        (s + days(1), "documents_outstanding", "in_progress"), (s + days(10), "documents_received", "in_progress"),
        (s + days(10) + hours(0.2), "documents_received", "error", "SYS-503", "software_fault", True),
        (s + days(10) + hours(2), "documents_received", "in_progress"),
        (s + days(10) + hours(2.1), "documents_received", "error", "SYS-503", "software_fault", True),
        (s + days(11), "documents_received", "in_progress"),
        (s + days(11) + hours(0.1), "documents_received", "error", "SYS-TIMEOUT", "software_fault", True),
    ])
    make_case(sink, ids, rng, ALEX, "ADV-007", "portal_problem", "technical_support", s + days(11) + hours(3), None, 1,
              end_at=cfg.as_of)
    make_case(sink, ids, rng, ALEX, "ADV-007", "complaint", "complaints_team", s + days(12), None, 2, end_at=cfg.as_of,
              contact_times=[s + days(12) + hours(0.1), s + days(19)])
    sink.observed_outcome.append(outcome_row(cfg, acct, "not_yet_observed", None, None, "none"))
    out["alex"] = {"mortgage_id": ALEX, "cycle_id": acct["cycle_id"], "application_id": app,
                   "checkpoint_at": (deal_end - days(90)).isoformat(),
                   "story": "Portal faults stop a product-transfer request. One failed collection. Unresolved complaint. Illustrative."}

    # Sam
    rng = np.random.default_rng([cfg.seed, 900002])
    deal_end = cfg.as_of.replace(year=2026, month=3, day=18, hour=0)
    acct = make_account(sink, ids, rng, SAM, deal_end, product="fixed_2y", cycle_no=3, joint=True, owner="ADV-012")
    rows = make_payments(sink, ids, rng, acct, start=cfg.history_start, end_at=cfg.as_of, onset=None, outages=[], clean=True)
    target = min(rows, key=lambda r: abs((r["due_at"] - deal_end.replace(year=2025, month=12, day=1)).days))
    rev = add_reversal(sink, ids, rng, target, target["posted_at"] + days(5), [], reason="bank_recall")
    add_manual_payment(sink, ids, rng, target, rev["posted_at"] + days(4), [])
    s = deal_end - days(100) + hours(10)
    app = _request(sink, ids, rng, SAM, [
        (s, "started", "in_progress"), (s + hours(0.5), "product_selected", "in_progress"),
        (s + days(1), "documents_outstanding", "in_progress"), (s + days(4), "documents_received", "in_progress"),
        (s + days(5), "offer_in_review", "in_progress"), (s + days(8), "offer_issued", "in_progress"),
        (s + days(10), "offer_accepted", "in_progress"), (deal_end - days(2) + hours(9), "completed", "completed"),
    ])
    done = deal_end - days(2) + hours(9)
    sink.observed_outcome.append(outcome_row(cfg, acct, "product_transfer", done, done + hours(20), "transfer_completion"))
    out["sam"] = {"mortgage_id": SAM, "cycle_id": acct["cycle_id"], "application_id": app,
                  "reversed_entry_id": target["entry_id"], "reversal_entry_id": rev["entry_id"],
                  "story": "Completed product transfer. One reversed collection, repaid. Neither proves default. Illustrative."}

    # Casey and Robin (T07)
    for key, mid, pid in (("casey", CASEY, 900003), ("robin", ROBIN, 900004)):
        rng = np.random.default_rng([cfg.seed, pid])
        deal_end = base + days(60)
        acct = make_account(sink, ids, rng, mid, deal_end, product="fixed_2y", cycle_no=2, joint=False)
        make_payments(sink, ids, rng, acct, start=cfg.history_start, end_at=cfg.as_of, onset=None, outages=[], clean=True)
        cp = deal_end - days(90)
        if key == "casey":
            case = make_case(sink, ids, rng, mid, acct["owner_id"], "complaint", "complaints_team", cp - days(50), None, 3,
                             end_at=cfg.as_of, contact_times=[cp - days(50) + hours(0.2), cp - days(35), cp - days(12)])
            out[key] = {"mortgage_id": mid, "cycle_id": acct["cycle_id"], "case_ids": [case],
                        "checkpoint_at": cp.isoformat(),
                        "story": "Three contacts about one unresolved complaint (T07). Illustrative."}
        else:
            cases = []
            for k, cat in enumerate(["general_enquiry", "billing_enquiry", "rate_enquiry"]):
                opened = cp - days(70 - 20 * k)
                cases.append(make_case(sink, ids, rng, mid, acct["owner_id"], cat, "general_servicing", opened,
                                       opened + days(1), 1, end_at=cfg.as_of, contact_times=[opened + hours(0.2)]))
            out[key] = {"mortgage_id": mid, "cycle_id": acct["cycle_id"], "case_ids": cases,
                        "checkpoint_at": cp.isoformat(),
                        "story": "Three contacts about three separate resolved enquiries (T07). Illustrative."}
        sink.observed_outcome.append(outcome_row(cfg, acct, "not_yet_observed", None, None, "none"))
    return out
