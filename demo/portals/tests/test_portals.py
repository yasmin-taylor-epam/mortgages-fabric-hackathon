"""Offline tests of the demo portals. They need the generated synthetic data in data/output (python -m hbdata ...)."""
from __future__ import annotations

import re

import pytest

from portals import config, events, reasons
from portals.board import Board
from portals.sources import is_live

pytest.importorskip("duckdb")
fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture(scope="module")
def settings():
    return config.Settings()


@pytest.fixture(scope="module")
def source(settings):
    if not (config.REPO / "data" / "output" / "lakehouse").exists():
        pytest.skip("No generated data in data/output")
    from portals.sources import OfflineSource
    s = OfflineSource(settings)
    yield s
    s.close()


@pytest.fixture(scope="module")
def client(settings, source):
    from portals.app import create_app
    return fastapi_testclient.TestClient(create_app(settings, source))


def test_events_match_the_contract(settings):
    at = events.next_demo_time(settings, None)
    for action, fault in (("renew", False), ("renew", True), ("payment_help", False)):
        env = events.build(action, "HB-M-900001", at, None, "documents_received", fault)
        assert events.validate(env) == []
        assert is_live(env["event_id"])
        assert env["occurred_at"].startswith("2026-10-16")


def test_demo_clock_is_after_the_data(settings):
    assert events.next_demo_time(settings, None).isoformat().startswith("2026-10-16T09:00")
    later = events.next_demo_time(settings, "2026-10-16T09:00:00Z")
    assert later.isoformat().startswith("2026-10-16T10:00")


def test_reasons_are_plain_words():
    assert reasons.direction(0.2) == "raises"
    assert reasons.direction(-0.2) == "lowers"
    assert "_" not in reasons.CASE_FAMILIES["support"]


def test_worklist_is_ordered_by_priority(settings, source):
    w = Board(settings, source).worklist()
    ranks = [r["priority_rank"] for r in w["renewals"]]
    assert ranks == sorted(ranks) and ranks
    assert all(r["reason"] for r in w["renewals"])
    assert w["today"].year == 2026 and w["today"].month == 10  # demo clock, not the real date
    assert all(r["days_left"] is None or r["days_left"] > -400 for r in w["renewals"])


def test_customer_page_for_the_three_personas(settings, source):
    b = Board(settings, source)
    alex = b.customer("HB-M-900001")
    assert alex["acct"] and alex["timeline"]
    c3121 = b.customer("HB-M-003121")
    assert c3121["score"] and c3121["score"]["priority_rank"] is not None
    assert any(k["action_family"] == "renewal" and k["assigned_to"] == "ADV-027" for k in c3121["cases"])
    sam = b.customer("HB-M-900002")
    assert sam["cases"] == []
    assert not b.problems, b.problems


def test_bad_mortgage_id_is_rejected(client):
    assert client.get("/staff/customer/x';drop").status_code in (400, 404)


@pytest.mark.parametrize("path", ["/", "/customer", "/customer?who=HB-M-003121", "/staff", "/staff/summary",
                                  "/staff/customer/HB-M-900001", "/staff/customer/HB-M-900002"])
def test_pages_render_and_explain_themselves(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text[:500]
    assert "Offline preview" in r.text
    if path != "/":
        assert "What this shows" in r.text
    assert "Some data could not be read" not in r.text


def test_alex_payment_help_opens_a_support_case(client, source):
    r = client.post("/customer/send", data={"who": "HB-M-900001", "action": "payment_help"}, follow_redirects=False)
    assert r.status_code == 303
    eid = r.headers["location"].rsplit("/", 1)[-1]
    assert re.fullmatch(r"PSE-97\d{7}", eid)
    arrival = client.get(f"/api/arrival/{eid}").json()
    assert arrival["accepted"] and arrival["service"]
    client.post("/staff/process", follow_redirects=False)
    page = client.get("/staff").text
    assert "from the demo" in page and "Payment support" in page
    # the same event again: stored twice, still one case (T01)
    client.post(f"/customer/resend/{eid}", follow_redirects=False)
    client.post("/staff/process", follow_redirects=False)
    cases = [c for c in source.sql("SELECT * FROM review_cases WHERE mortgage_id = ?", ("HB-M-900001",))
             if c["action_family"] == "support"]
    assert len(cases) == 1
    sent = client.get(f"/customer/sent/{eid}?again=1").text
    assert "second time" in sent


def test_lab_account_blocked_unless_allowed():
    with pytest.raises(SystemExit):
        config.check_account("odl_user_1@sandboxailabs1.onmicrosoft.com")
    config.check_account("odl_user_1@sandboxailabs1.onmicrosoft.com", allow_lab_account=True)
    config.check_account("someone@example.com")


def test_load_reads_allow_lab_account(tmp_path):
    path = tmp_path / "config.local.yml"
    path.write_text("login_hint: odl_user_1@sandboxailabs1.onmicrosoft.com\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        config.load(path)
    path.write_text("login_hint: odl_user_1@sandboxailabs1.onmicrosoft.com\nallow_lab_account: true\n", encoding="utf-8")
    assert config.load(path).allow_lab_account is True
