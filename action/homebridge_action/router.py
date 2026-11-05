"""Payment-support-first routing for HomeBridge review cases.

`route` is a pure function. It reads one candidate event and the current account
context. It returns one decision. It writes nothing and sends nothing.

Routing order (blueprint section 05):
1. Active payment support goes to trained servicing staff, whatever the score (INV-1).
2. A verified renewal-portal software fault goes to technical support.
3. An eligible external-refinancing risk candidate goes to the servicing adviser.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .models import (
    COMPLETED_RUN,
    AccountContext,
    ActionFamily,
    Candidate,
    IdentityStatus,
    Outcome,
    PaymentSupportStatus,
    RiskUpdated,
    RoutingDecision,
    RoutingPolicy,
    ServiceEvent,
    ServiceEventType,
)


def route(
    candidate: Candidate,
    context: Optional[AccountContext],
    policy: RoutingPolicy,
    now: datetime,
    run_status: Optional[str] = None,
) -> RoutingDecision:
    def decide(outcome: Outcome, reason: str, family: Optional[ActionFamily] = None,
               assigned_to: Optional[str] = None, holds: tuple[ActionFamily, ...] = (),
               rechecks: tuple[ActionFamily, ...] = ()) -> RoutingDecision:
        return RoutingDecision(
            event_id=candidate.event_id,
            mortgage_id=candidate.mortgage_id,
            cycle_id=candidate.cycle_id,
            outcome=outcome,
            reason=reason,
            policy_version=policy.version,
            action_family=family,
            assigned_to=assigned_to,
            hold_families=holds,
            recheck_families=rechecks,
        )

    # T02: an unknown account or a conflicting borrower mapping goes to quarantine.
    if context is None or context.identity_status == IdentityStatus.UNKNOWN:
        return decide(Outcome.QUARANTINE, "unknown_identity")
    if context.identity_status == IdentityStatus.CONFLICT:
        return decide(Outcome.QUARANTINE, "conflicting_identity")
    if (context.mortgage_id, context.cycle_id) != (candidate.mortgage_id, candidate.cycle_id):
        return decide(Outcome.QUARANTINE, "context_key_mismatch")

    if isinstance(candidate, ServiceEvent):
        return _route_service(candidate, policy, decide)
    return _route_risk(candidate, context, policy, now, run_status, decide)


def _route_service(event: ServiceEvent, policy: RoutingPolicy, decide) -> RoutingDecision:
    kind = event.event_type
    if kind == ServiceEventType.PAYMENT_SUPPORT_REQUESTED:
        # INV-1: no score, threshold or balance is read on this path.
        return decide(Outcome.CREATE_CASE, "payment_support_requested",
                      ActionFamily.SUPPORT, policy.support_queue, holds=(ActionFamily.RENEWAL,))
    if kind == ServiceEventType.PORTAL_FAULT_VERIFIED:
        return decide(Outcome.CREATE_CASE, "verified_portal_fault",
                      ActionFamily.TECHNICAL, policy.technical_queue)
    if kind == ServiceEventType.PAYMENT_REVERSED:
        # T04: a reversal is not proof of default. Flag open cases and request rescoring.
        return decide(Outcome.RECHECK, "payment_reversed_rescore_requested",
                      rechecks=(ActionFamily.RENEWAL, ActionFamily.SUPPORT))
    # A support request that closes does not become a renewal review. A person releases any hold.
    return decide(Outcome.NO_CASE, "payment_support_closed_no_commercial_review")


def _route_risk(event: RiskUpdated, context: AccountContext, policy: RoutingPolicy,
                now: datetime, run_status: Optional[str], decide) -> RoutingDecision:
    # INV-1: active payment support takes priority before any score check.
    if context.payment_support_status == PaymentSupportStatus.ACTIVE:
        return decide(Outcome.CREATE_CASE, "active_payment_support_precedes_renewal",
                      ActionFamily.SUPPORT, policy.support_queue, holds=(ActionFamily.RENEWAL,))
    # INV-5: only a completed run can produce an eligible risk candidate.
    if run_status != COMPLETED_RUN:
        return decide(Outcome.NO_CASE, f"run_not_completed:{run_status or 'unknown'}")
    if now - event.scored_at > policy.max_score_age:
        return decide(Outcome.NO_CASE, "stale_score")
    if context.payment_support_status == PaymentSupportStatus.UNKNOWN:
        return decide(Outcome.HOLD, "payment_support_status_unknown")
    if event.score < policy.risk_threshold:
        return decide(Outcome.NO_CASE, "below_threshold")
    if not context.owner_id:
        return decide(Outcome.HOLD, "no_review_owner")
    return decide(Outcome.CREATE_CASE, "eligible_refinancing_risk",
                  ActionFamily.RENEWAL, context.owner_id)
