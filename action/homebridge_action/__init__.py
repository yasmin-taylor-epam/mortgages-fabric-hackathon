"""Local reference implementation of HomeBridge payment-support-first routing and the review-case writer.

This code runs locally. It has not run in Fabric, Activator, Power Automate or SharePoint.
"""

from .case_writer import CaseWriter, WriteResult
from .models import (
    AccountContext,
    ActionFamily,
    CaseStatus,
    ContractError,
    Outcome,
    ReviewCase,
    RiskUpdated,
    RoutingDecision,
    RoutingPolicy,
    ServiceEvent,
    ServiceEventType,
    candidate_from_dict,
    make_case_key,
)
from .router import route
from .store import DuplicateCaseKey, SQLiteCaseStore, TransientWriteError

__all__ = [
    "AccountContext", "ActionFamily", "CaseStatus", "CaseWriter", "ContractError", "DuplicateCaseKey",
    "Outcome", "ReviewCase", "RiskUpdated", "RoutingDecision", "RoutingPolicy", "SQLiteCaseStore",
    "ServiceEvent", "ServiceEventType", "TransientWriteError", "WriteResult", "candidate_from_dict",
    "make_case_key", "route",
]
