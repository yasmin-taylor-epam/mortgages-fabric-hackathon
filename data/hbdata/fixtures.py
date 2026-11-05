"""Replay fixtures for failure cases T01 to T07, the INV-2 cutoff check and the demo.

Each case file in data/fixtures/cases/ holds small synthetic inputs and the
expected result. Mini fixtures use mortgage IDs HB-M-8xxxxx so they never clash
with generated data. The demo replay files continue the generated persona Alex.
Run `python -m hbdata fixtures` to rebuild them; tests fail if the committed
files drift from this builder.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import contracts, generator, io, personas, replay
from .validation import SOURCE_DATASETS

FIXTURES_DIR = contracts.DATA_DIR / "fixtures"


def _acct(mid: str, deal_end: str, cycle: str = "C02", owner: str = "ADV-001") -> dict:
    end = pd.Timestamp(deal_end)
    start = end - pd.DateOffset(months=24)
    return {"account_key": f"{mid}|{cycle}", "mortgage_id": mid, "cycle_id": cycle, "cohort_id": end.strftime("%Y-%m"),
            "product_type": "fixed_2y", "repayment_type": "repayment", "deal_start_at": _z(start), "deal_end_at": _z(end),
            "loan_end_at": "2049-01-01T00:00:00Z", "originated_at": "2022-01-01T00:00:00Z", "fixed_rate_pct": 4.8,
            "follow_on_rate_pct": 7.49, "balance": 180000.0, "balance_as_of": _z(start), "current_payment": 1100.0,
            "owner_id": owner, "recorded_at": _z(start + pd.Timedelta(hours=2)), "ingested_at": _z(start + pd.Timedelta(hours=3))}


def _party(mid: str, pid: str, role: str = "primary", valid_from: str = "2022-01-01T00:00:00Z") -> dict:
    return {"mortgage_id": mid, "party_id": pid, "role": role, "valid_from": valid_from, "valid_to": None,
            "mapping_status": "approved", "recorded_at": valid_from, "ingested_at": valid_from}


def _z(ts) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pay(eid, mid, due, posted, ingested, due_amt, recv, entry_type="scheduled_collection", reversal_of=None, reason=None):
    return {"entry_id": eid, "mortgage_id": mid, "entry_type": entry_type, "due_at": due, "posted_at": posted,
            "ingested_at": ingested, "amount_due": due_amt, "amount_received": recv, "reversal_of": reversal_of, "reason_code": reason}


def _tre(eid, app, mid, occurred, ingested, stage, status, code=None, cls=None, verified=None):
    return {"event_id": eid, "application_id": app, "mortgage_id": mid, "occurred_at": occurred, "ingested_at": ingested,
            "stage": stage, "status": status, "error_code": code, "error_class": cls, "fault_verified": verified, "channel": "portal"}


def _case_ev(eid, case, mid, changed, recorded, category, status, route="general_servicing", owner="ADV-001"):
    return {"case_event_id": eid, "case_id": case, "mortgage_id": mid, "changed_at": changed, "recorded_at": recorded,
            "category": category, "status": status, "owner_id": owner, "support_route": route}


def _contact(cid, case, mid, occurred, ingested, channel="phone"):
    return {"contact_id": cid, "case_id": case, "mortgage_id": mid, "occurred_at": occurred, "ingested_at": ingested,
            "channel": channel, "direction": "inbound"}


def _env(eid, etype, mid, occurred, ingested, payload, app=None, source=None, sent=None):
    return {"event_id": eid, "event_type": etype, "schema_version": "1.0",
            "source_system": source or {"transfer_request_event": "renewal_portal", "payment_entry_posted": "payment_ledger"}.get(etype, "servicing"),
            "mortgage_id": mid, "application_id": app, "occurred_at": occurred, "sent_at": sent or occurred,
            "simulated_ingested_at": ingested, "payload": payload}


def _fault_payload(stage="documents_received", code="SYS-503", verified=True):
    return {"stage": stage, "status": "error", "error_code": code, "error_class": "software_fault", "fault_verified": verified, "channel": "portal"}


# ------------------------------------------------------------------ cases

def case_t01() -> dict:
    mid = "HB-M-800101"
    ev = _env("PTE-900000101", "transfer_request_event", mid, "2026-10-20T09:00:00Z", "2026-10-20T09:00:30Z",
              _fault_payload(), app="PTR-9000101")
    dup = {**ev, "sent_at": "2026-10-20T09:05:00Z", "simulated_ingested_at": "2026-10-20T09:05:30Z"}
    return {"id": "T01", "title": "The same eligible event is replayed.",
            "rule": "One review case for the same case key.", "behaviour_owner": ["Data and replay", "Dashboard and action"],
            "inputs": {"mortgage_account": [_acct(mid, "2027-01-10T00:00:00Z")], "account_party": [_party(mid, "HB-P-8001011")]},
            "live_events": [ev, dup],
            "expected": {"raw_event_rows": 2, "service_event_count": 1,
                         "service_events": [{"event_id": "PTE-900000101", "event_type": "portal_fault_verified", "cycle_id": "C02"}],
                         "journey_timeline_rows": 1, "dashboard_review_cases_for_key": {"HB-M-800101|C02|technical": 1}}}


def case_t02() -> dict:
    a, b = "HB-M-800201", "HB-M-800202"
    return {"id": "T02", "title": "An unknown account or a conflicting borrower mapping arrives.",
            "rule": "The record goes to quarantine.", "behaviour_owner": ["Data and replay"],
            "inputs": {
                "mortgage_account": [_acct(a, "2027-01-15T00:00:00Z"), _acct(b, "2027-01-15T00:00:00Z")],
                "account_party": [_party(a, "HB-P-8002011"), _party(a, "HB-P-8002012", valid_from="2023-06-01T00:00:00Z"),
                                  _party(b, "HB-P-8002021")],
                "payment": [_pay("PAY-900000201", "HB-M-899999", "2026-09-01T00:00:00Z", "2026-09-01T10:00:00Z", "2026-09-01T12:00:00Z", 900.0, 900.0)],
                "servicing_case_event": [_case_ev("SCE-900000201", "CASE-9000201", b, "2026-09-02T09:00:00Z", "2026-09-02T09:10:00Z", "general_enquiry", "opened")],
                "servicing_contact": [_contact("CON-90000201", "CASE-9000299", b, "2026-09-02T09:00:00Z", "2026-09-02T09:05:00Z")],
            },
            "live_events": [
                _env("PTE-900000201", "transfer_request_event", "HB-M-899998", "2026-10-20T09:00:00Z", "2026-10-20T09:00:30Z",
                     _fault_payload(), app="PTR-9000201"),
                _env("PTE-900000202", "transfer_request_event", a, "2026-10-20T09:01:00Z", "2026-10-20T09:01:30Z",
                     _fault_payload(), app="PTR-9000202"),
            ],
            "expected": {"quarantine_by_reason": {"unknown_account": 1, "conflicting_borrower_mapping": 2, "unknown_case_link": 1},
                         "identity_status": {a: "conflict", b: "ok"},
                         "live_accepted": 0, "live_quarantine_by_reason": {"unknown_account": 1, "conflicting_borrower_mapping": 1}}}


def case_t03() -> dict:
    a, b = "HB-M-800301", "HB-M-800302"
    pays = [_pay("PAY-900000301", b, "2026-08-01T00:00:00Z", "2026-08-01T10:00:00Z", "2026-08-01T12:00:00Z", 1100.0, 0.0, reason="insufficient_funds"),
            _pay("PAY-900000302", b, "2026-09-01T00:00:00Z", "2026-09-01T10:00:00Z", "2026-09-01T12:00:00Z", 1100.0, 1100.0),
            _pay("PAY-900000303", b, "2026-10-01T00:00:00Z", "2026-10-01T10:00:00Z", "2026-10-15T12:00:00Z", 1100.0, 1100.0)]
    return {"id": "T03", "title": "The payment feed is stale or missing.",
            "rule": "The view flags the feed as stale. A missing feed is not zero arrears.", "behaviour_owner": ["Data and replay", "Dashboard and action"],
            "scenarios": {
                "missing": {"cutoff_at": "2026-10-10T00:00:00Z",
                            "inputs": {"mortgage_account": [_acct(a, "2027-01-08T00:00:00Z")], "account_party": [_party(a, "HB-P-8003011")]},
                            "expected": {a: {"ctx_payment_feed_stale": True, "ctx_payment_feed_watermark": None,
                                             "ctx_missed_payments_180d": None, "ctx_arrears_amount": None},
                                         "feed_watermark": {"dataset": "payment", "is_stale": True, "row_count": 0}}},
                "stale": {"cutoff_at": "2026-10-10T00:00:00Z",
                          "inputs": {"mortgage_account": [_acct(b, "2027-01-08T00:00:00Z")], "account_party": [_party(b, "HB-P-8003021")], "payment": pays},
                          "expected": {b: {"ctx_payment_feed_stale": True, "ctx_payment_feed_watermark": "2026-09-01T12:00:00Z",
                                           "ctx_missed_payments_180d": None, "ctx_arrears_amount": None}}},
                "fresh": {"cutoff_at": "2026-10-16T00:00:00Z",
                          "inputs": {"mortgage_account": [_acct(b, "2027-01-14T00:00:00Z")], "account_party": [_party(b, "HB-P-8003021")], "payment": pays},
                          "expected": {b: {"ctx_payment_feed_stale": False, "ctx_missed_payments_180d": 1, "ctx_arrears_amount": 1100.0}}},
            }}


def case_t04() -> dict:
    mid = "HB-M-800401"
    pays = [_pay("PAY-900000401", mid, "2026-09-01T00:00:00Z", "2026-09-01T10:00:00Z", "2026-09-01T12:00:00Z", 1100.0, 1100.0),
            _pay("PAY-900000402", mid, "2026-09-01T00:00:00Z", "2026-09-06T10:00:00Z", "2026-09-06T12:00:00Z", 0.0, -1100.0,
                 entry_type="reversal", reversal_of="PAY-900000401", reason="indemnity_claim"),
            _pay("PAY-900000403", mid, "2026-09-29T00:00:00Z", "2026-09-29T10:00:00Z", "2026-09-29T12:00:00Z", 1100.0, 1100.0),
            _pay("PAY-900000404", mid, "2026-09-29T00:00:00Z", "2026-09-30T10:00:00Z", "2026-09-30T12:00:00Z", 0.0, -1100.0,
                 entry_type="reversal", reversal_of="PAY-900000999", reason="bank_recall")]
    live = _env("PAY-900000405", "payment_entry_posted", mid, "2026-10-20T10:00:00Z", "2026-10-20T10:00:20Z",
                {"entry_type": "reversal", "due_at": "2026-09-29T00:00:00Z", "amount_due": 0.0, "amount_received": -1100.0,
                 "reversal_of": "PAY-900000403", "reason_code": "bank_recall"})
    return {"id": "T04", "title": "A payment is reversed.", "rule": "Review and rescoring start. The audit record stays.",
            "behaviour_owner": ["Data and replay", "Dashboard and action", "Models and evaluation"],
            "cutoff_at": "2026-10-01T00:00:00Z",
            "inputs": {"mortgage_account": [_acct(mid, "2026-12-30T00:00:00Z")], "account_party": [_party(mid, "HB-P-8004011")], "payment": pays},
            "live_events": [live],
            "expected": {"clean_payment_entries": ["PAY-900000401", "PAY-900000402", "PAY-900000403"],
                         "quarantine": {"PAY-900000404": "invalid_reversal"},
                         "snapshot": {"ctx_reversals_180d": 1, "ctx_missed_payments_180d": 1, "ctx_arrears_amount": 1100.0},
                         "service_events": [{"event_id": "PAY-900000405", "event_type": "payment_reversed",
                                             "evidence_ids": ["PAY-900000405", "PAY-900000403"]}],
                         "note": "A reversal is not proof of default. It creates no new review case by itself. Dashboard flags open support and renewal cases for a recheck and records rescore_requested with these evidence IDs; Models rescores. A new renewal review starts only if the rescore publishes an eligible risk_updated event."}}


def case_t05() -> dict:
    mid = "HB-M-800501"
    return {"id": "T05", "title": "A scoring run fails.",
            "rule": "The last score is marked stale. The live view still updates. No new risk_updated event fires. Service alerts remain independent.",
            "behaviour_owner": ["Models and evaluation", "Data and replay", "Dashboard and action"],
            "copied_at": "2026-10-20T08:30:00Z",
            "inputs": {"mortgage_account": [_acct(mid, "2027-01-10T00:00:00Z")], "account_party": [_party(mid, "HB-P-8005011")]},
            "gold": {
                "score_runs": [{"run_id": "RUN-20261020-0800", "run_kind": "scheduled", "status": "completed",
                                "started_at": "2026-10-20T08:00:00Z", "completed_at": "2026-10-20T08:04:00Z"},
                               {"run_id": "RUN-20261020-0815", "run_kind": "scheduled", "status": "failed",
                                "started_at": "2026-10-20T08:15:00Z", "completed_at": None}],
                "account_scores": [{"account_key": f"{mid}|C02", "mortgage_id": mid, "cycle_id": "C02", "run_id": "RUN-20261020-0800",
                                    "snapshot_id": "SNP-0123456789abcdef", "model_version": "hb-refi-clf:1", "score": 0.41,
                                    "scored_at": "2026-10-20T08:03:00Z", "cutoff_at": "2026-10-20T08:00:00Z"},
                                   {"account_key": f"{mid}|C02", "mortgage_id": mid, "cycle_id": "C02", "run_id": "RUN-20261020-0815",
                                    "snapshot_id": "SNP-fedcba9876543210", "model_version": "hb-refi-clf:1", "score": 0.77,
                                    "scored_at": "2026-10-20T08:16:00Z", "cutoff_at": "2026-10-20T08:15:00Z"}]},
            "live_events": [_env("PSE-900000501", "payment_support_requested", mid, "2026-10-20T08:20:00Z", "2026-10-20T08:20:15Z",
                                 {"case_id": "CASE-9000501", "support_route": "payment_support", "channel": "phone"})],
            "expected": {"score_context": {"run_id": "RUN-20261020-0800", "score": 0.41, "score_state": "stale"},
                         "service_events": [{"event_id": "PSE-900000501", "event_type": "payment_support_requested"}],
                         "models_publishes_risk_updated_for_failed_run": False}}


def case_t06() -> dict:
    mid = "HB-M-800601"
    event = {"event_id": "RUP-20261020-800601", "event_type": "risk_updated", "schema_version": "1.0", "mortgage_id": mid,
             "cycle_id": "C02", "run_id": "RUN-20261020-0800", "snapshot_id": "SNP-0123456789abcdef", "model_version": "hb-refi-clf:1",
             "cluster_model_version": "hb-seg:1", "segment_id": "S2", "scored_at": "2026-10-20T08:03:00Z",
             "cutoff_at": "2026-10-20T08:00:00Z", "target": "confirmed_external_refinancing", "score": 0.83,
             "evidence_ids": ["PTR-9000601", "CASE-9000601"]}
    return {"id": "T06", "title": "A review-case write fails.", "rule": "The write retries with the same key. No duplicate review case appears.",
            "behaviour_owner": ["Dashboard and action"],
            "inputs": {"risk_updated": [event]},
            "simulated_failures": [{"attempt": 1, "result": "timeout"}, {"attempt": 2, "result": "duplicate_key"}],
            "expected": {"case_key": f"{mid}|C02|renewal", "event_id_on_every_attempt": "RUP-20261020-800601", "review_cases_for_key": 1}}


def case_t07() -> dict:
    a, b = "HB-M-800701", "HB-M-800702"
    cut = pd.Timestamp("2026-10-01T00:00:00Z")
    t = lambda d, h=9: _z(cut - pd.Timedelta(days=d) + pd.Timedelta(hours=h))  # noqa: E731
    cases = [
        _case_ev("SCE-900000701", "CASE-9000701", a, t(50), t(50, 10), "complaint", "opened", "complaints_team"),
        _case_ev("SCE-900000702", "CASE-9000701", a, t(49), t(49, 10), "complaint", "in_progress", "complaints_team"),
        _case_ev("SCE-900000703", "CASE-9000701", a, t(30), t(30, 10), "complaint", "in_progress", "complaints_team"),
        _case_ev("SCE-900000711", "CASE-9000711", b, t(70), t(70, 10), "general_enquiry", "opened"),
        _case_ev("SCE-900000712", "CASE-9000711", b, t(69), t(69, 10), "general_enquiry", "resolved"),
        _case_ev("SCE-900000713", "CASE-9000712", b, t(50), t(50, 10), "billing_enquiry", "opened"),
        _case_ev("SCE-900000714", "CASE-9000712", b, t(49), t(49, 10), "billing_enquiry", "resolved"),
        _case_ev("SCE-900000715", "CASE-9000713", b, t(30), t(30, 10), "rate_enquiry", "opened"),
        _case_ev("SCE-900000716", "CASE-9000713", b, t(29), t(29, 10), "rate_enquiry", "resolved"),
    ]
    contacts = [
        _contact("CON-90000701", "CASE-9000701", a, t(50), t(50, 10)),
        _contact("CON-90000702", "CASE-9000701", a, t(35), t(35, 10)),
        _contact("CON-90000703", "CASE-9000701", a, t(12), t(12, 10)),
        _contact("CON-90000711", "CASE-9000711", b, t(70), t(70, 10)),
        _contact("CON-90000712", "CASE-9000712", b, t(50), t(50, 10)),
        _contact("CON-90000713", "CASE-9000713", b, t(30), t(30, 10)),
    ]
    return {"id": "T07", "title": "Three contacts concern one unresolved problem. Three other contacts concern separate enquiries.",
            "rule": "The history distinguishes repeat contacts about one case from contacts about separate cases. A case state change alone does not count as a contact.",
            "behaviour_owner": ["Data and replay"], "cutoff_at": _z(cut),
            "inputs": {"mortgage_account": [_acct(a, "2026-12-30T00:00:00Z"), _acct(b, "2026-12-30T00:00:00Z")],
                       "account_party": [_party(a, "HB-P-8007011"), _party(b, "HB-P-8007021")],
                       "servicing_case_event": cases, "servicing_contact": contacts},
            "expected": {a: {"svc_contacts_90d": 3, "svc_max_contacts_per_case_365d": 3, "svc_repeat_contacts_unresolved": 2,
                             "svc_open_cases_at_cutoff": 1, "svc_unresolved_complaint": True, "svc_cases_opened_365d": 1},
                         b: {"svc_contacts_90d": 3, "svc_max_contacts_per_case_365d": 1, "svc_repeat_contacts_unresolved": 0,
                             "svc_open_cases_at_cutoff": 0, "svc_unresolved_complaint": False, "svc_cases_opened_365d": 3}}}


def case_cutoff() -> dict:
    mid = "HB-M-800801"
    cut = "2026-10-01T00:00:00Z"
    return {"id": "CUT01", "title": "Inputs known only after the cutoff are excluded (INV-2).",
            "rule": "Both occurred_at and ingested_at must be at or before cutoff_at. A resolution recorded after the cutoff is not an input.",
            "behaviour_owner": ["Data and replay"], "cutoff_at": cut,
            "inputs": {
                "mortgage_account": [_acct(mid, "2026-12-30T00:00:00Z")], "account_party": [_party(mid, "HB-P-8008011")],
                "transfer_request_event": [
                    _tre("PTE-900000801", "PTR-9000801", mid, "2026-09-20T09:00:00Z", "2026-09-20T09:00:10Z", "started", "in_progress"),
                    _tre("PTE-900000802", "PTR-9000801", mid, "2026-09-21T09:00:00Z", "2026-09-21T09:00:10Z", "product_selected", "in_progress"),
                    _tre("PTE-900000803", "PTR-9000801", mid, "2026-09-30T20:00:00Z", "2026-10-02T08:00:00Z", "documents_outstanding", "error",
                         "SYS-503", "software_fault", True),
                    _tre("PTE-900000804", "PTR-9000801", mid, "2026-10-03T09:00:00Z", "2026-10-03T09:00:10Z", "documents_outstanding", "in_progress")],
                "servicing_case_event": [
                    _case_ev("SCE-900000801", "CASE-9000801", mid, "2026-09-10T09:00:00Z", "2026-09-10T09:05:00Z", "complaint", "opened", "complaints_team"),
                    _case_ev("SCE-900000802", "CASE-9000801", mid, "2026-09-29T09:00:00Z", "2026-10-03T09:00:00Z", "complaint", "resolved", "complaints_team")],
                "servicing_contact": [
                    _contact("CON-90000801", "CASE-9000801", mid, "2026-09-10T09:00:00Z", "2026-09-10T09:01:00Z"),
                    _contact("CON-90000802", "CASE-9000801", mid, "2026-09-30T15:00:00Z", "2026-10-01T06:00:00Z")]},
            "expected": {"pt_request_count": 1, "pt_latest_stage": "product_selected", "pt_software_errors_90d": 0,
                         "svc_unresolved_complaint": True, "svc_open_cases_at_cutoff": 1, "svc_contacts_90d": 1,
                         "excluded_event_ids": ["PTE-900000803", "PTE-900000804", "SCE-900000802", "CON-90000802"]}}


CASE_BUILDERS = [case_t01, case_t02, case_t03, case_t04, case_t05, case_t06, case_t07, case_cutoff]


def frames_from(inputs: dict) -> dict[str, pd.DataFrame]:
    """Contract-typed frames for all source datasets. Missing datasets are empty."""
    return {name: io.coerce(pd.DataFrame(inputs.get(name, [])), name) for name in SOURCE_DATASETS}


def load_case(case_id: str) -> dict:
    return json.loads((FIXTURES_DIR / "cases" / f"{case_id}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ demo replay (continues persona Alex)

def demo_events(cfg: generator.GenConfig = generator.GenConfig()) -> dict[str, list[dict]]:
    frames, manifest = generator.generate(cfg)
    alex = manifest["personas"]["alex"]
    t0 = pd.Timestamp(cfg.as_of) + pd.Timedelta(days=1, hours=9)
    z = lambda d: _z(t0 + pd.Timedelta(minutes=d))  # noqa: E731
    retry = _env("PTE-900000001", "transfer_request_event", personas.ALEX, z(0), z(0.5),
                 {"stage": "documents_received", "status": "in_progress", "error_code": None, "error_class": None,
                  "fault_verified": None, "channel": "portal"}, app=alex["application_id"])
    fault = _env("PTE-900000002", "transfer_request_event", personas.ALEX, z(2), z(2.5), _fault_payload(), app=alex["application_id"])
    support = _env("PSE-900000003", "payment_support_requested", personas.ALEX, z(120), z(120.25),
                   {"case_id": "CASE-9000003", "support_route": "payment_support", "channel": "phone"})
    duplicate = {**fault, "sent_at": z(30), "simulated_ingested_at": z(30.5)}
    return {"demo_alex.jsonl": [retry, fault, support], "demo_alex_duplicate.jsonl": [duplicate]}


def write_fixtures() -> dict:
    written = []
    for build in CASE_BUILDERS:
        case = {"synthetic": True, "fictional_company": "HomeBridge", **build()}
        path = FIXTURES_DIR / "cases" / f"{case['id']}.json"
        io.write_json(case, path)
        written.append(path.name)
    for name, rows in demo_events().items():
        replay.write_jsonl(rows, FIXTURES_DIR / "replay" / name)
        written.append(name)
    return {"written": written}
