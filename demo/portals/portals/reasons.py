"""Plain-English words for the model's feature names.

A feature attribution says how much one fact moved the score. A positive contribution makes the
customer more likely to refinance elsewhere. A negative one makes it less likely.
"""
from __future__ import annotations

STAGES = {
    "product_selected": "chose a new product",
    "documents_outstanding": "has documents still to send",
    "documents_received": "sent documents, waiting for the next step",
    "offer_in_review": "offer is being reviewed",
    "offer_issued": "has an offer to accept",
    "offer_accepted": "accepted the offer",
}
STATUSES = {"in_progress": "still in progress", "abandoned": "abandoned", "rejected": "rejected"}

NUMERIC = {
    "deal_length_months": "Current deal length: {v:.0f} months",
    "rate_gap_pct": "Rate rise when the deal ends: {v:.2f} percentage points",
    "pt_abandoned_or_rejected_count": "Renewal requests abandoned or rejected: {v:.0f}",
    "pt_customer_input_errors_90d": "Form errors by the customer in 90 days: {v:.0f}",
    "pt_days_at_current_stage": "Days stuck at the current renewal step: {v:.0f}",
    "pt_days_since_start": "Days since the renewal request started: {v:.0f}",
    "pt_failed_attempts_30d": "Failed renewal attempts in 30 days: {v:.0f}",
    "pt_failed_attempts_90d": "Failed renewal attempts in 90 days: {v:.0f}",
    "pt_failed_ratio_30_90": "Share of 90-day failures in the last 30 days: {v:.0%}",
    "pt_max_stage_dwell_days": "Longest wait at one renewal step: {v:.0f} days",
    "pt_request_count": "Renewal requests started: {v:.0f}",
    "pt_request_started": "Renewal request started: {yn}",
    "svc_cases_billing_365d": "Billing cases in 12 months: {v:.0f}",
    "svc_cases_complaint_365d": "Complaints in 12 months: {v:.0f}",
    "svc_cases_general_365d": "General enquiries in 12 months: {v:.0f}",
    "svc_cases_opened_365d": "Service cases in 12 months: {v:.0f}",
    "svc_cases_portal_365d": "Portal problem cases in 12 months: {v:.0f}",
    "svc_cases_rate_365d": "Rate enquiries in 12 months: {v:.0f}",
    "svc_contact_trend": "Change in contact volume: {v:+.0f}",
    "svc_contacts_90d": "Contacts with us in 90 days: {v:.0f}",
    "svc_max_contacts_per_case_365d": "Most contacts on one case: {v:.0f}",
    "svc_oldest_unresolved_complaint_age_days": "Age of the oldest open complaint: {v:.0f} days",
    "svc_open_cases_at_cutoff": "Open service cases: {v:.0f}",
    "svc_repeat_contacts_unresolved": "Repeat contacts on unresolved cases: {v:.0f}",
    "svc_unresolved_complaint": "Has an unresolved complaint: {yn}",
}


def describe(feature_name: str, feature_value: float | None) -> str:
    """One short sentence for one model input, for example 'Renewal step: sent documents, waiting for the next step'."""
    name, _, level = feature_name.partition("=")
    if name == "pt_latest_stage" and level:
        return f"Renewal step: {STAGES.get(level, level.replace('_', ' '))}"
    if name == "pt_latest_status" and level:
        return f"Renewal request: {STATUSES.get(level, level.replace('_', ' '))}"
    template = NUMERIC.get(name)
    if not template:
        return feature_name.replace("_", " ")
    v = float(feature_value or 0.0)
    return template.format(v=v, yn="yes" if v >= 0.5 else "no")


def direction(contribution: float) -> str:
    return "raises" if contribution > 0 else "lowers"


def top_reason(rows: list[dict]) -> str:
    """The strongest fact that raises the score. Rows are attribution rows for one account."""
    up = sorted((r for r in rows if (r.get("contribution") or 0) > 0), key=lambda r: -r["contribution"])
    return describe(up[0]["feature_name"], up[0].get("feature_value")) if up else "No single strong reason"


CASE_FAMILIES = {
    "renewal": "Renewal outreach: call before the deal ends",
    "support": "Payment support: the customer asked for help with payments",
    "technical": "Portal fault: our renewal website failed for this customer",
}
CASE_STATUSES = {"open": "Open", "on_hold": "On hold", "closed": "Closed", "resolved": "Resolved"}
EVENT_TYPES = {
    "transfer_request_event": "Renewal website step",
    "payment_support_requested": "Asked for payment help",
    "portal_fault_verified": "Renewal website fault (checked)",
    "risk_updated": "New risk score",
}


def event_text(event_type: str, payload: dict | None) -> str:
    p = payload or {}
    if event_type == "transfer_request_event":
        stage = STAGES.get(p.get("stage") or "", p.get("stage") or "unknown step")
        if p.get("status") == "error":
            kind = "our system failed" if p.get("error_class") == "software_fault" else "the customer made an input error"
            return f"Renewal website error ({p.get('error_code') or 'no code'}): {kind}. Step: {stage}."
        return f"Renewal website: {stage} ({STATUSES.get(p.get('status') or '', p.get('status') or '')})."
    if event_type == "payment_support_requested":
        return f"Asked for help with payments by {(p.get('channel') or 'unknown channel').replace('_', ' ')}. Case {p.get('case_id') or '-'}."
    return EVENT_TYPES.get(event_type, event_type)
