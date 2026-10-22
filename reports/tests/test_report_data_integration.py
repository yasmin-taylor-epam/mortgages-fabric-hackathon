"""Integration with the Data and replay package (data/).

- Every column that the report reads from a Data and replay table exists in that table's contract.
- The T03 fixture scenarios give the expected payment-feed state and arrears.
- The report rules run on the generated clean tables and agree with the producer's own case summary.

The tests skip when data/ has no contracts, fixtures or generated output.
Set HOMEBRIDGE_DATA_DIR to point at another data/ folder.
"""

import json
import os
import re
from pathlib import Path

import duckdb
import pytest

REPORTS = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("HOMEBRIDGE_DATA_DIR", REPORTS.parent / "data")).resolve()
CONTRACTS = DATA_DIR / "contracts"
CLEAN = DATA_DIR / "output" / "lakehouse" / "clean"
DATA_AND_REPLAY_TABLES = [
    "mortgage", "mortgage_account", "payment", "feed_watermark", "transfer_request_event",
    "servicing_case", "servicing_case_event", "servicing_contact",
]

needs_contracts = pytest.mark.skipif(not (CONTRACTS / "mortgage_account.schema.json").exists(),
                                     reason=f"Data and replay contracts not found at {CONTRACTS}")


def gold_columns():
    text = (REPORTS / "logic" / "gold_schema.sql").read_text(encoding="utf-8")
    result = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", text, re.S):
        cols = []
        for line in m.group(2).splitlines():
            line = line.split("--")[0].strip()
            if line and not line.startswith("PRIMARY KEY"):
                cols.append(line.split()[0])
        result[m.group(1)] = cols
    return result


def new_db():
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute((REPORTS / "logic" / "gold_schema.sql").read_text(encoding="utf-8"))
    con.execute((REPORTS / "logic" / "views.sql").read_text(encoding="utf-8"))
    return con


def insert_rows(con, table, rows):
    cols = gold_columns()[table]
    for row in rows:
        con.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [row.get(c) for c in cols])


def rows(con, sql, params=None):
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


@needs_contracts
@pytest.mark.parametrize("table", DATA_AND_REPLAY_TABLES)
def test_report_columns_exist_in_the_producer_contract(table):
    schema = json.loads((CONTRACTS / f"{table}.schema.json").read_text(encoding="utf-8"))
    missing = set(gold_columns()[table]) - set(schema["properties"])
    assert not missing, f"{table}: report reads {sorted(missing)}, which the contract does not define"


@pytest.mark.skipif(not (DATA_DIR / "fixtures" / "cases" / "T03.json").exists(), reason="T03 fixture not found")
@pytest.mark.parametrize("scenario", ["missing", "stale", "fresh"])
def test_t03_payment_feed_scenarios(scenario):
    case = json.loads((DATA_DIR / "fixtures" / "cases" / "T03.json").read_text(encoding="utf-8"))["scenarios"][scenario]
    con = new_db()
    accounts = case["inputs"]["mortgage_account"]
    insert_rows(con, "mortgage", [{"mortgage_id": a["mortgage_id"], "product_type": a["product_type"],
                                   "current_cycle_id": a["cycle_id"]} for a in accounts])
    payments = case["inputs"].get("payment", [])
    insert_rows(con, "payment", payments)
    cutoff = case["cutoff_at"]
    known = [p["ingested_at"] for p in payments if p["ingested_at"] <= cutoff]
    # The report applies its own age check, so is_stale stays FALSE here and the rule must still find staleness.
    insert_rows(con, "feed_watermark", [{
        "dataset": "payment", "checked_at": cutoff, "max_ingested_at": max(known) if known else None,
        "max_event_at": None, "row_count": len(known), "stale_threshold_hours": 72.0, "is_stale": False,
    }])

    (mortgage_id, expected), = [(k, v) for k, v in case["expected"].items() if k.startswith("HB-M-")]
    position, = rows(con, "SELECT * FROM payment_position(CAST(? AS TIMESTAMPTZ)::TIMESTAMP) WHERE mortgage_id = ?",
                     [cutoff, mortgage_id])

    assert (position["payment_state"] != "current") == expected["ctx_payment_feed_stale"]
    if expected["ctx_arrears_amount"] is None:
        assert position["arrears_amount"] is None
    else:
        assert float(position["arrears_amount"]) == pytest.approx(expected["ctx_arrears_amount"])
    if scenario == "missing":
        assert position["payment_state"] == "missing"


@pytest.fixture(scope="module")
def generated():
    if not (CLEAN / "mortgage_account.parquet").exists():
        pytest.skip("generated data not found. Run: cd data; python -m hbdata all")
    con = new_db()
    for table in DATA_AND_REPLAY_TABLES:
        cols = ", ".join(gold_columns()[table])
        con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM read_parquet(?)", [str(CLEAN / f"{table}.parquet")])
    as_of = con.execute("SELECT max(as_of_at)::TIMESTAMP FROM read_parquet(?)", [str(CLEAN / "servicing_case.parquet")]).fetchone()[0]
    yield con, as_of
    con.close()


def test_account_view_runs_on_generated_data(generated):
    con, as_of = generated
    total = con.execute("SELECT count(*) FROM mortgage_account").fetchone()[0]
    view = rows(con, "SELECT * FROM account_view(?, 1440)", [as_of])
    assert len(view) == total and len({r["account_key"] for r in view}) == total
    assert {r["score_status"] for r in view} == {"no_score"}
    assert {r["payment_state"] for r in view} <= {"current", "no_records"}
    alex = [r for r in view if r["mortgage_id"] == "HB-M-900001"]
    assert alex and all(r["checkpoint_at"] is not None for r in alex)


def test_support_history_agrees_with_the_producer_case_summary(generated):
    con, as_of = generated
    mine = {r["case_id"]: r for r in rows(con, "SELECT * FROM support_history(?)", [as_of])}
    theirs = {r["case_id"]: r for r in rows(
        con, "SELECT case_id, is_resolved, contact_count FROM read_parquet(?)", [str(CLEAN / "servicing_case.parquet")])}
    assert set(mine) == set(theirs)
    assert [c for c in mine if mine[c]["contacts"] != theirs[c]["contact_count"]] == []
    assert [c for c in mine if mine[c]["is_resolved"] != theirs[c]["is_resolved"]] == []


def test_payment_reversals_net_against_the_original(generated):
    con, _ = generated
    unmatched = con.execute("""
        SELECT count(*) FROM payment AS r
        LEFT JOIN payment AS o ON o.entry_id = r.reversal_of
        WHERE r.entry_type = 'reversal'
          AND (o.entry_id IS NULL OR r.amount_due <> 0 OR r.amount_received <> -o.amount_received)
    """).fetchone()[0]
    assert unmatched == 0
