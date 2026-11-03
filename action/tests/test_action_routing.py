"""Routing tests: INV-1, INV-5 (consumer side), INV-6 and T02."""

import ast
import dataclasses
import inspect
from datetime import timedelta
from pathlib import Path

import pytest

from action_helpers import NOW, POLICY, make_context, make_risk, make_service
from homebridge_action import ActionFamily, Outcome, ReviewCase, route
from homebridge_action.models import AccountContext, RoutingDecision

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "homebridge_action"


class TestInv1PaymentSupportFirst:
    @pytest.mark.parametrize("score", [0.0, 0.05, 0.59, 0.6, 0.99, 1.0])
    def test_active_support_routes_to_support_whatever_the_score(self, score):
        decision = route(make_risk(score=score), make_context(support="active"), POLICY, NOW, "completed")
        assert decision.outcome == Outcome.CREATE_CASE
        assert decision.action_family == ActionFamily.SUPPORT
        assert decision.assigned_to == POLICY.support_queue
        assert ActionFamily.RENEWAL in decision.hold_families

    @pytest.mark.parametrize("run_status", ["completed", "failed", None])
    def test_support_does_not_wait_for_scoring(self, run_status):
        old = NOW - timedelta(days=30)
        decision = route(make_risk(scored_at=old), make_context(support="active"), POLICY, NOW, run_status)
        assert decision.action_family == ActionFamily.SUPPORT

    def test_support_request_event_routes_to_trained_staff(self):
        decision = route(make_service("payment_support_requested"), make_context(), POLICY, NOW)
        assert decision.outcome == Outcome.CREATE_CASE
        assert decision.action_family == ActionFamily.SUPPORT
        assert decision.hold_families == (ActionFamily.RENEWAL,)

    def test_routing_inputs_hold_no_balance_or_loan_value(self):
        fields = {f.name for f in dataclasses.fields(AccountContext)}
        assert not fields & {"balance", "loan_balance", "current_payment", "exposure"}
        assert "balance" not in inspect.signature(route).parameters

    def test_support_closed_does_not_create_a_renewal_review(self):
        decision = route(make_service("payment_support_closed"), make_context(), POLICY, NOW)
        assert decision.outcome == Outcome.NO_CASE
        assert decision.action_family is None


class TestRoutingOrder:
    def test_verified_portal_fault_goes_to_technical_support(self):
        decision = route(make_service("portal_fault_verified"), make_context(), POLICY, NOW)
        assert decision.action_family == ActionFamily.TECHNICAL
        assert decision.assigned_to == POLICY.technical_queue

    def test_eligible_risk_goes_to_the_servicing_adviser(self):
        decision = route(make_risk(score=0.8), make_context(owner="ADV-9"), POLICY, NOW, "completed")
        assert decision.outcome == Outcome.CREATE_CASE
        assert decision.action_family == ActionFamily.RENEWAL
        assert decision.assigned_to == "ADV-9"

    def test_below_threshold_creates_no_case(self):
        decision = route(make_risk(score=0.59), make_context(), POLICY, NOW, "completed")
        assert decision.outcome == Outcome.NO_CASE
        assert decision.reason == "below_threshold"

    def test_unknown_support_status_holds_the_renewal_review(self):
        decision = route(make_risk(score=0.9), make_context(support="unknown"), POLICY, NOW, "completed")
        assert decision.outcome == Outcome.HOLD
        assert decision.reason == "payment_support_status_unknown"

    def test_missing_owner_holds_the_renewal_review(self):
        decision = route(make_risk(score=0.9), make_context(owner=None), POLICY, NOW, "completed")
        assert decision.outcome == Outcome.HOLD
        assert decision.reason == "no_review_owner"

    def test_payment_reversal_requests_recheck_not_a_new_case(self):
        decision = route(make_service("payment_reversed"), make_context(), POLICY, NOW)
        assert decision.outcome == Outcome.RECHECK
        assert decision.action_family is None


class TestInv5ScoreEligibility:
    @pytest.mark.parametrize("status", ["failed", "running", None])
    def test_event_from_an_incomplete_run_creates_no_case(self, status):
        decision = route(make_risk(score=0.95), make_context(), POLICY, NOW, status)
        assert decision.outcome == Outcome.NO_CASE
        assert decision.reason.startswith("run_not_completed")

    def test_stale_score_creates_no_renewal_case(self):
        stale = NOW - POLICY.max_score_age - timedelta(minutes=1)
        decision = route(make_risk(score=0.95, scored_at=stale), make_context(), POLICY, NOW, "completed")
        assert decision.outcome == Outcome.NO_CASE
        assert decision.reason == "stale_score"


class TestT02Quarantine:
    def test_unknown_account_goes_to_quarantine(self):
        decision = route(make_risk(), None, POLICY, NOW, "completed")
        assert decision.outcome == Outcome.QUARANTINE
        assert decision.reason == "unknown_identity"

    def test_conflicting_borrower_mapping_goes_to_quarantine(self):
        decision = route(make_risk(), make_context(identity="conflict"), POLICY, NOW, "completed")
        assert decision.outcome == Outcome.QUARANTINE
        assert decision.reason == "conflicting_identity"

    def test_context_for_another_cycle_goes_to_quarantine(self):
        decision = route(make_risk(cycle_id="CYC-OTHER"), make_context(), POLICY, NOW, "completed")
        assert decision.outcome == Outcome.QUARANTINE

    def test_quarantine_beats_payment_support(self):
        decision = route(make_service("payment_support_requested"), make_context(identity="unknown"), POLICY, NOW)
        assert decision.outcome == Outcome.QUARANTINE


class TestInv6NoCustomerAction:
    FORBIDDEN = {"message", "email", "sms", "letter", "rate", "offer", "credit", "contact_script"}

    def test_decision_never_authorises_customer_contact(self):
        for decision in (
            route(make_risk(), make_context(), POLICY, NOW, "completed"),
            route(make_service("payment_support_requested"), make_context(), POLICY, NOW),
            route(make_service("portal_fault_verified"), make_context(), POLICY, NOW),
        ):
            assert decision.customer_contact == "not_authorised"

    def test_case_and_decision_have_no_message_rate_or_credit_fields(self):
        for cls in (ReviewCase, RoutingDecision):
            names = {f.name for f in dataclasses.fields(cls)}
            assert not {n for n in names if any(word in n for word in self.FORBIDDEN)}

    def test_package_imports_no_messaging_or_network_modules(self):
        banned = {"smtplib", "email", "urllib", "http", "requests", "socket", "httpx", "aiohttp"}
        for path in PACKAGE_DIR.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    roots = {node.module.split(".")[0]}
                else:
                    continue
                assert not roots & banned, f"{path.name} imports {roots & banned}"
