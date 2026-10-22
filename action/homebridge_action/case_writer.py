"""Idempotent review-case writer.

The writer mirrors the proposed Power Automate flow (action/power_automate/review-case-writer.md):
1. Record the alert time.
2. Read the current account context and run status. Do this on every delivery and every retry.
3. Route the candidate (router.route) and audit the decision.
4. Write the case with the key mortgage_id + cycle_id + action_family.
   - A duplicate key reads the existing case (T01, INV-3).
   - A transient failure retries with the same key. Each failure is visible in the exception register (T06).
   - Before each retry, steps 2 and 3 run again. A change in payment-support status can change the route.
5. Apply holds and recheck flags. Earlier audit rows never change.

The writer sends no customer message and makes no rate or credit decision (INV-6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .models import (
    AccountContext,
    ActionFamily,
    Candidate,
    CaseStatus,
    Outcome,
    ReviewCase,
    RiskUpdated,
    RoutingDecision,
    RoutingPolicy,
    ServiceEvent,
    make_case_key,
)
from .router import route
from .store import CaseStore, DuplicateCaseKey, TransientWriteError

ContextLookup = Callable[[str, str], Optional[AccountContext]]
RunStatusLookup = Callable[[str], Optional[str]]
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class WriteResult:
    decision: RoutingDecision
    case: Optional[ReviewCase] = None
    created: bool = False
    error: Optional[str] = None


class CaseWriter:
    def __init__(self, store: CaseStore, context_lookup: ContextLookup, run_status_lookup: RunStatusLookup,
                 policy: RoutingPolicy, clock: Clock = utc_now, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.store = store
        self.context_lookup = context_lookup
        self.run_status_lookup = run_status_lookup
        self.policy = policy
        self.clock = clock
        self.max_attempts = max_attempts

    def handle(self, candidate: Candidate) -> WriteResult:
        alert_received_at = self.clock()
        decision = self._decide(candidate, "decision")
        if decision.outcome != Outcome.CREATE_CASE:
            return self._finish_without_case(candidate, decision)

        case_key = None
        for attempt in range(1, self.max_attempts + 1):
            if attempt > 1:
                # Recheck identity, payment-support status, run status and freshness before each retry.
                decision = self._decide(candidate, "decision_rechecked")
                if decision.outcome != Outcome.CREATE_CASE:
                    return self._finish_without_case(candidate, decision)
            case = self._build_case(candidate, decision, alert_received_at)
            case_key = case.case_key
            try:
                self.store.insert_case(case)
            except DuplicateCaseKey:
                existing = self.store.get_case(case.case_key)
                self._audit(candidate, decision, "duplicate_key_read_existing", decision.reason,
                            {"existing_source_event_id": existing.source_event_id if existing else None,
                             "attempt": attempt}, case.case_key)
                self._hold(candidate, decision)
                return WriteResult(decision, existing, created=False)
            except TransientWriteError as exc:
                self._exception(candidate, case.case_key, "case_write_failed", str(exc) or "transient", attempt)
                continue
            self._audit(candidate, decision, "case_created", decision.reason,
                        {"assigned_to": case.assigned_to, "action_family": case.action_family.value,
                         "attempt": attempt, "store": case.store}, case.case_key)
            self._hold(candidate, decision)
            return WriteResult(decision, self.store.get_case(case.case_key), created=True)

        message = f"retries exhausted after {self.max_attempts} attempts"
        self._exception(candidate, case_key, "case_write_retries_exhausted", message, self.max_attempts)
        self._audit(candidate, decision, "case_write_failed", message, {}, case_key)
        return WriteResult(decision, None, created=False, error=message)

    def _decide(self, candidate: Candidate, action: str) -> RoutingDecision:
        context = self.context_lookup(candidate.mortgage_id, candidate.cycle_id)
        run_status = self.run_status_lookup(candidate.run_id) if isinstance(candidate, RiskUpdated) else None
        decision = route(candidate, context, self.policy, self.clock(), run_status)
        self._audit(candidate, decision, action, decision.reason, self._decision_details(candidate, decision, context))
        return decision

    def _finish_without_case(self, candidate: Candidate, decision: RoutingDecision) -> WriteResult:
        if decision.outcome == Outcome.QUARANTINE:
            self._exception(candidate, None, "quarantine", decision.reason)
        elif decision.outcome == Outcome.HOLD:
            self._exception(candidate, None, "renewal_review_held", decision.reason)
        elif decision.outcome == Outcome.RECHECK:
            self._recheck(candidate, decision)
        return WriteResult(decision)

    def _build_case(self, candidate: Candidate, decision: RoutingDecision, alert_received_at: datetime) -> ReviewCase:
        family = ActionFamily(decision.action_family)
        case = ReviewCase(
            case_key=make_case_key(candidate.mortgage_id, candidate.cycle_id, family),
            mortgage_id=candidate.mortgage_id,
            cycle_id=candidate.cycle_id,
            action_family=family,
            status=CaseStatus.OPEN,
            assigned_to=str(decision.assigned_to),
            routing_policy=decision.policy_version,
            decision_reason=decision.reason,
            source_event_id=candidate.event_id,
            evidence_ids=candidate.evidence_ids,
            alert_received_at=alert_received_at,
            case_created_at=self.clock(),
            ingested_at=candidate.ingested_at,
        )
        if isinstance(candidate, RiskUpdated):
            case.scored_at = candidate.scored_at
            case.model_version = candidate.model_version
            case.cluster_model_version = candidate.cluster_model_version
            case.run_id = candidate.run_id
            case.snapshot_id = candidate.snapshot_id
        return case


    def _hold(self, candidate: Candidate, decision: RoutingDecision) -> None:
        # Payment support takes priority over a commercial renewal task. Keep the task; record its hold.
        for family in decision.hold_families:
            key = make_case_key(candidate.mortgage_id, candidate.cycle_id, family)
            existing = self.store.get_case(key)
            if existing is not None and existing.status == CaseStatus.OPEN:
                self.store.set_status(key, CaseStatus.ON_HOLD)
                self._audit(candidate, decision, "case_held", "payment_support_priority",
                            {"previous_status": existing.status.value}, key)

    def _recheck(self, candidate: Candidate, decision: RoutingDecision) -> None:
        for family in decision.recheck_families:
            key = make_case_key(candidate.mortgage_id, candidate.cycle_id, family)
            existing = self.store.get_case(key)
            if existing is not None and existing.status != CaseStatus.CLOSED:
                self.store.flag_recheck(key)
                self._audit(candidate, decision, "case_flagged_recheck", decision.reason, {}, key)
        self._audit(candidate, decision, "rescore_requested", decision.reason,
                    {"mortgage_id": candidate.mortgage_id, "cycle_id": candidate.cycle_id})

    def _decision_details(self, candidate: Candidate, decision: RoutingDecision,
                          context: Optional[AccountContext]) -> dict:
        details = {
            "outcome": decision.outcome.value,
            "action_family": decision.action_family.value if decision.action_family else None,
            "assigned_to": decision.assigned_to,
            "evidence_ids": list(candidate.evidence_ids),
            "customer_contact": decision.customer_contact,
            "context_version": context.context_version if context else None,
            "ingested_at": candidate.ingested_at.isoformat() if candidate.ingested_at else None,
        }
        if isinstance(candidate, RiskUpdated):
            details.update({
                "model_version": candidate.model_version,
                "cluster_model_version": candidate.cluster_model_version,
                "run_id": candidate.run_id,
                "snapshot_id": candidate.snapshot_id,
                "scored_at": candidate.scored_at.isoformat(),
                "score": candidate.score,
            })
        elif isinstance(candidate, ServiceEvent):
            details["event_type"] = candidate.event_type.value
            details["occurred_at"] = candidate.occurred_at.isoformat()
        return details

    def _audit(self, candidate: Candidate, decision: RoutingDecision, action: str, reason: str,
               details: dict, case_key: Optional[str] = None) -> None:
        self.store.append_audit({
            "recorded_at": self.clock(),
            "event_id": candidate.event_id,
            "case_key": case_key,
            "action": action,
            "reason": reason,
            "routing_policy": decision.policy_version,
            "details": details,
        })

    def _exception(self, candidate: Candidate, case_key: Optional[str], kind: str, message: str,
                   attempt: Optional[int] = None) -> None:
        self.store.append_exception({
            "recorded_at": self.clock(),
            "event_id": candidate.event_id,
            "case_key": case_key,
            "kind": kind,
            "message": message,
            "attempt": attempt,
        })
