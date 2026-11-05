"""Model features from the frozen feature snapshot (feature_version fv1).

Inputs follow blueprint section 04: product-transfer request friction, renewal-
portal errors, servicing history and contacts. Deal terms add the rate gap and
the deal length.

Excluded on purpose:
- ctx_* columns. They are payment and routing context (INV-1), not refinancing
  inputs. Payment difficulty is never a hidden refinancing label.
- days_to_deal_end. Every checkpoint snapshot has 90, so it has no signal, and
  live snapshots would feed values the model never saw.
- balance, current_payment and other loan-size fields. Loan balance is exposure,
  not a reason to leave, and it can act as a wealth proxy.
- identifiers, evidence lists and timestamps.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_VERSION = "fv1"

NUMERIC = [
    "rate_gap_pct", "deal_length_months",
    "pt_request_count", "pt_days_since_start", "pt_days_at_current_stage", "pt_max_stage_dwell_days",
    "pt_failed_attempts_30d", "pt_failed_attempts_90d", "pt_failed_ratio_30_90",
    "pt_software_errors_90d", "pt_retries_after_error_90d", "pt_customer_input_errors_90d",
    "pt_abandoned_or_rejected_count",
    "svc_cases_opened_365d", "svc_open_cases_at_cutoff", "svc_oldest_unresolved_complaint_age_days",
    "svc_cases_general_365d", "svc_cases_billing_365d", "svc_cases_rate_365d", "svc_cases_complaint_365d",
    "svc_cases_portal_365d", "svc_contacts_30d", "svc_contacts_90d", "svc_contact_trend",
    "svc_max_contacts_per_case_365d", "svc_repeat_contacts_unresolved",
]
BOOLEAN = ["pt_request_started", "pt_completed", "pt_unrecovered_software_error", "svc_unresolved_complaint"]
CATEGORICAL = {
    "pt_latest_stage": ["started", "product_selected", "documents_outstanding", "documents_received",
                        "offer_in_review", "offer_issued", "offer_accepted"],
    "pt_latest_status": ["in_progress", "abandoned", "rejected", "error"],
}
EXCLUDED_PREFIXES = ("ctx_",)
EXCLUDED = ["days_to_deal_end", "balance", "current_payment", "fixed_rate_pct", "follow_on_rate_pct",
            "months_since_origination", "years_to_loan_end", "borrower_count", "product_type"]

# Cluster on request and servicing behaviour only (blueprint section 04).
CLUSTER_FEATURES = [c for c in NUMERIC + BOOLEAN if c.startswith(("pt_", "svc_"))]

SOURCE_COLUMNS = NUMERIC + BOOLEAN + list(CATEGORICAL)


def feature_names() -> list[str]:
    names = NUMERIC + BOOLEAN
    for col, cats in CATEGORICAL.items():
        names += [f"{col}={c}" for c in cats]
    return names


def build_matrix(snap: pd.DataFrame) -> pd.DataFrame:
    """Numeric model matrix in a fixed column order. Missing values become 0.

    Only the named model inputs are selected, so label or outcome columns in
    the frame never reach the model.

    A null in a request or complaint field means that no request or complaint
    exists at the cutoff. The boolean flags carry that fact, so 0 is safe.
    """
    missing = [c for c in SOURCE_COLUMNS if c not in snap.columns]
    if missing:
        raise KeyError(f"Snapshot lacks model input columns: {missing}")
    out = pd.DataFrame(index=snap.index)
    for c in NUMERIC:
        out[c] = pd.to_numeric(snap[c], errors="coerce").astype(float).fillna(0.0)
    for c in BOOLEAN:
        out[c] = snap[c].fillna(False).astype(bool).astype(float)
    for col, cats in CATEGORICAL.items():
        v = snap[col].astype(object)
        for cat in cats:
            out[f"{col}={cat}"] = (v == cat).astype(float)
    out = out[feature_names()]
    if not np.isfinite(out.to_numpy()).all():
        raise ValueError("Model matrix holds non-finite values.")
    return out


def cluster_matrix(snap: pd.DataFrame) -> pd.DataFrame:
    """Behavioural inputs for clustering. Counts use log1p to reduce skew."""
    x = build_matrix(snap)[CLUSTER_FEATURES].copy()
    for c in CLUSTER_FEATURES:
        if c not in BOOLEAN and c != "svc_contact_trend":
            x[c] = np.log1p(x[c].clip(lower=0))
    x["svc_contact_trend"] = np.sign(x["svc_contact_trend"]) * np.log1p(x["svc_contact_trend"].abs())
    return x
