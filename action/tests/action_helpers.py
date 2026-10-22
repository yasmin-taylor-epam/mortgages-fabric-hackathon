"""Shared builders for the action tests. All values are synthetic."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from homebridge_action import (
    AccountContext,
    CaseWriter,
    RiskUpdated,
    RoutingPolicy,
    ServiceEvent,
    ServiceEventType,
    SQLiteCaseStore,
)
from homebridge_action.models import IdentityStatus, PaymentSupportStatus

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
POLICY = RoutingPolicy(version="test-policy", risk_threshold=0.6, max_score_age=timedelta(hours=24))


def make_context(mortgage_id="MTG-1", cycle_id="CYC-1", identity="ok", support="none", owner="ADV-1"):
    return AccountContext(mortgage_id, cycle_id, IdentityStatus(identity), PaymentSupportStatus(support), owner, "ctx-test")


def make_risk(event_id="EVT-R-1", mortgage_id="MTG-1", cycle_id="CYC-1", score=0.8, run_id="RUN-OK",
              scored_at=NOW - timedelta(minutes=30), evidence=("EV-1",)):
    return RiskUpdated(
        event_id=event_id, mortgage_id=mortgage_id, cycle_id=cycle_id, run_id=run_id, snapshot_id="SNP-1",
        model_version="clf-test", cluster_model_version="clu-test", segment_id="SEG-1", scored_at=scored_at,
        cutoff_at=scored_at - timedelta(minutes=5), target="external_refinancing_90d", score=score,
        evidence_ids=tuple(evidence), ingested_at=scored_at + timedelta(minutes=1),
    )


def make_service(event_type, event_id="EVT-S-1", mortgage_id="MTG-1", cycle_id="CYC-1"):
    return ServiceEvent(
        event_id=event_id, event_type=ServiceEventType(event_type), mortgage_id=mortgage_id, cycle_id=cycle_id,
        occurred_at=NOW - timedelta(minutes=20), ingested_at=NOW - timedelta(minutes=19), evidence_ids=("EV-S-1",),
    )


class Harness:
    """A writer over an in-memory store with a mutable context."""

    def __init__(self, store=None, max_attempts=3):
        self.store = store or SQLiteCaseStore()
        self.contexts = {("MTG-1", "CYC-1"): make_context()}
        self.runs = {"RUN-OK": "completed", "RUN-FAILED": "failed"}
        self.writer = CaseWriter(
            self.store,
            context_lookup=lambda m, c: self.contexts.get((m, c)),
            run_status_lookup=self.runs.get,
            policy=POLICY,
            clock=lambda: NOW,
            max_attempts=max_attempts,
        )

    def set_support(self, status):
        self.contexts[("MTG-1", "CYC-1")] = make_context(support=status)
