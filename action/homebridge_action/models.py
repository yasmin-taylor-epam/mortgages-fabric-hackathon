"""Data shapes for the HomeBridge review-case writer.

The field names follow action/contracts/consumer-contract.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Union


class ContractError(ValueError):
    """An input does not match the consumer contract."""


class ActionFamily(str, Enum):
    SUPPORT = "support"
    TECHNICAL = "technical"
    RENEWAL = "renewal"


class Outcome(str, Enum):
    CREATE_CASE = "create_case"
    QUARANTINE = "quarantine"
    NO_CASE = "no_case"
    HOLD = "hold"
    RECHECK = "recheck"


class ServiceEventType(str, Enum):
    PAYMENT_SUPPORT_REQUESTED = "payment_support_requested"
    PAYMENT_SUPPORT_CLOSED = "payment_support_closed"
    PORTAL_FAULT_VERIFIED = "portal_fault_verified"
    PAYMENT_REVERSED = "payment_reversed"


class IdentityStatus(str, Enum):
    OK = "ok"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


class PaymentSupportStatus(str, Enum):
    ACTIVE = "active"
    NONE = "none"
    UNKNOWN = "unknown"


class CaseStatus(str, Enum):
    OPEN = "open"
    ON_HOLD = "on_hold"
    CLOSED = "closed"


COMPLETED_RUN = "completed"

# INV-6: the prototype never authorises customer contact.
CUSTOMER_CONTACT_NOT_AUTHORISED = "not_authorised"


def parse_ts(value: Any, name: str) -> datetime:
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError(f"{name} is not an ISO 8601 time: {value!r}") from exc
    else:
        raise ContractError(f"{name} is missing or not a time")
    if ts.tzinfo is None:
        raise ContractError(f"{name} must carry a time zone")
    return ts.astimezone(timezone.utc)


def _opt_ts(value: Any, name: str) -> Optional[datetime]:
    return None if value is None else parse_ts(value, name)


def _require(data: Mapping[str, Any], names: tuple[str, ...], kind: str) -> None:
    missing = [n for n in names if data.get(n) in (None, "")]
    if missing:
        raise ContractError(f"{kind} is missing {', '.join(missing)}")


def _evidence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ContractError("evidence_ids must be a list")
    return tuple(str(v) for v in value)


@dataclass(frozen=True)
class RiskUpdated:
    event_id: str
    mortgage_id: str
    cycle_id: str
    run_id: str
    snapshot_id: str
    model_version: str
    cluster_model_version: str
    segment_id: str
    scored_at: datetime
    cutoff_at: datetime
    target: str
    score: float
    evidence_ids: tuple[str, ...] = ()
    ingested_at: Optional[datetime] = None

    REQUIRED = (
        "event_id", "mortgage_id", "cycle_id", "run_id", "snapshot_id",
        "model_version", "cluster_model_version", "segment_id",
        "scored_at", "cutoff_at", "target", "score",
    )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RiskUpdated":
        _require(data, cls.REQUIRED, "risk_updated")
        score = float(data["score"])
        if not 0.0 <= score <= 1.0:
            raise ContractError("score must be between 0 and 1")
        return cls(
            event_id=str(data["event_id"]),
            mortgage_id=str(data["mortgage_id"]),
            cycle_id=str(data["cycle_id"]),
            run_id=str(data["run_id"]),
            snapshot_id=str(data["snapshot_id"]),
            model_version=str(data["model_version"]),
            cluster_model_version=str(data["cluster_model_version"]),
            segment_id=str(data["segment_id"]),
            scored_at=parse_ts(data["scored_at"], "scored_at"),
            cutoff_at=parse_ts(data["cutoff_at"], "cutoff_at"),
            target=str(data["target"]),
            score=score,
            evidence_ids=_evidence(data.get("evidence_ids")),
            ingested_at=_opt_ts(data.get("ingested_at"), "ingested_at"),
        )


@dataclass(frozen=True)
class ServiceEvent:
    event_id: str
    event_type: ServiceEventType
    mortgage_id: str
    cycle_id: str
    occurred_at: datetime
    ingested_at: datetime
    evidence_ids: tuple[str, ...] = ()

    REQUIRED = ("event_id", "event_type", "mortgage_id", "cycle_id", "occurred_at", "ingested_at")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ServiceEvent":
        _require(data, cls.REQUIRED, "service event")
        try:
            event_type = ServiceEventType(data["event_type"])
        except ValueError as exc:
            raise ContractError(f"unknown event_type {data['event_type']!r}") from exc
        return cls(
            event_id=str(data["event_id"]),
            event_type=event_type,
            mortgage_id=str(data["mortgage_id"]),
            cycle_id=str(data["cycle_id"]),
            occurred_at=parse_ts(data["occurred_at"], "occurred_at"),
            ingested_at=parse_ts(data["ingested_at"], "ingested_at"),
            evidence_ids=_evidence(data.get("evidence_ids")),
        )


Candidate = Union[RiskUpdated, ServiceEvent]


SERVICE_EVENT_TYPES = {t.value for t in ServiceEventType}


def candidate_from_dict(data: Mapping[str, Any]) -> Candidate:
    """Read one candidate in the contract shape. event_type selects risk_updated or a service event."""
    kind = data.get("event_type")
    if kind == "risk_updated":
        return RiskUpdated.from_dict(data)
    if kind in SERVICE_EVENT_TYPES:
        return ServiceEvent.from_dict(data)
    raise ContractError(f"unknown event_type {kind!r}")


@dataclass(frozen=True)
class AccountContext:
    """Current state read at routing time. It holds no balance or score (INV-1)."""

    mortgage_id: str
    cycle_id: str
    identity_status: IdentityStatus
    payment_support_status: PaymentSupportStatus
    owner_id: Optional[str]
    context_version: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AccountContext":
        _require(data, ("mortgage_id", "cycle_id", "identity_status", "payment_support_status"), "account context")
        return cls(
            mortgage_id=str(data["mortgage_id"]),
            cycle_id=str(data["cycle_id"]),
            identity_status=IdentityStatus(data["identity_status"]),
            payment_support_status=PaymentSupportStatus(data["payment_support_status"]),
            owner_id=data.get("owner_id") or None,
            context_version=str(data.get("context_version", "")),
        )


@dataclass(frozen=True)
class RoutingPolicy:
    """Routing settings. The risk threshold is frozen on validation data by the models work package."""

    version: str
    risk_threshold: float
    max_score_age: timedelta
    support_queue: str = "payment-support-team"
    technical_queue: str = "renewal-portal-technical-support"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RoutingPolicy":
        _require(data, ("version", "risk_threshold", "max_score_age_minutes"), "routing policy")
        return cls(
            version=str(data["version"]),
            risk_threshold=float(data["risk_threshold"]),
            max_score_age=timedelta(minutes=float(data["max_score_age_minutes"])),
            support_queue=str(data.get("support_queue", cls.support_queue)),
            technical_queue=str(data.get("technical_queue", cls.technical_queue)),
        )


@dataclass(frozen=True)
class RoutingDecision:
    event_id: str
    mortgage_id: str
    cycle_id: str
    outcome: Outcome
    reason: str
    policy_version: str
    action_family: Optional[ActionFamily] = None
    assigned_to: Optional[str] = None
    hold_families: tuple[ActionFamily, ...] = ()
    recheck_families: tuple[ActionFamily, ...] = ()
    customer_contact: str = CUSTOMER_CONTACT_NOT_AUTHORISED


def make_case_key(mortgage_id: str, cycle_id: str, family: ActionFamily) -> str:
    return f"{mortgage_id}|{cycle_id}|{ActionFamily(family).value}"


@dataclass
class ReviewCase:
    """An internal review case. It authorises no customer contact, rate or credit decision (INV-6)."""

    case_key: str
    mortgage_id: str
    cycle_id: str
    action_family: ActionFamily
    status: CaseStatus
    assigned_to: str
    routing_policy: str
    decision_reason: str
    source_event_id: str
    evidence_ids: tuple[str, ...]
    alert_received_at: datetime
    case_created_at: datetime
    ingested_at: Optional[datetime] = None
    scored_at: Optional[datetime] = None
    model_version: Optional[str] = None
    cluster_model_version: Optional[str] = None
    run_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    needs_recheck: bool = False
    store: str = "local"
