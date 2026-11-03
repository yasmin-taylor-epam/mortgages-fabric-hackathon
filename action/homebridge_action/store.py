"""Local stand-in for the SharePoint review-case list.

SQLite enforces a unique case_key, as the SharePoint list does. Every case row
records store = 'local'. Nothing here claims that SharePoint wrote a case.
The audit log is append-only: triggers reject UPDATE and DELETE.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional, Protocol

from .models import ActionFamily, CaseStatus, ReviewCase


class DuplicateCaseKey(Exception):
    """The store already holds a case with this case_key."""


class TransientWriteError(Exception):
    """A write failed and may succeed on retry with the same key."""


class CaseStore(Protocol):
    def insert_case(self, case: ReviewCase) -> None: ...
    def get_case(self, case_key: str) -> Optional[ReviewCase]: ...
    def set_status(self, case_key: str, status: CaseStatus) -> None: ...
    def flag_recheck(self, case_key: str) -> None: ...
    def append_audit(self, entry: dict[str, Any]) -> None: ...
    def append_exception(self, entry: dict[str, Any]) -> None: ...
    def list_cases(self) -> list[ReviewCase]: ...
    def list_audit(self) -> list[dict[str, Any]]: ...
    def list_exceptions(self) -> list[dict[str, Any]]: ...


SCHEMA = """
CREATE TABLE IF NOT EXISTS review_cases (
    case_key              TEXT NOT NULL PRIMARY KEY,
    mortgage_id           TEXT NOT NULL,
    cycle_id              TEXT NOT NULL,
    action_family         TEXT NOT NULL CHECK (action_family IN ('support', 'technical', 'renewal')),
    status                TEXT NOT NULL CHECK (status IN ('open', 'on_hold', 'closed')),
    assigned_to           TEXT NOT NULL,
    routing_policy        TEXT NOT NULL,
    decision_reason       TEXT NOT NULL,
    source_event_id       TEXT NOT NULL,
    evidence_ids          TEXT NOT NULL,
    alert_received_at     TEXT NOT NULL,
    case_created_at       TEXT NOT NULL,
    ingested_at           TEXT,
    scored_at             TEXT,
    model_version         TEXT,
    cluster_model_version TEXT,
    run_id                TEXT,
    snapshot_id           TEXT,
    needs_recheck         INTEGER NOT NULL DEFAULT 0,
    store                 TEXT NOT NULL DEFAULT 'local'
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at     TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    case_key        TEXT,
    action          TEXT NOT NULL,
    reason          TEXT NOT NULL,
    routing_policy  TEXT NOT NULL,
    details         TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TABLE IF NOT EXISTS exception_register (
    exception_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at   TEXT NOT NULL,
    event_id      TEXT NOT NULL,
    case_key      TEXT,
    kind          TEXT NOT NULL,
    message       TEXT NOT NULL,
    attempt       INTEGER
);
"""

_CASE_COLUMNS = (
    "case_key", "mortgage_id", "cycle_id", "action_family", "status", "assigned_to",
    "routing_policy", "decision_reason", "source_event_id", "evidence_ids",
    "alert_received_at", "case_created_at", "ingested_at", "scored_at", "model_version",
    "cluster_model_version", "run_id", "snapshot_id", "needs_recheck", "store",
)


def _ts(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else value.isoformat()


def _from_ts(value: Optional[str]) -> Optional[datetime]:
    return None if value is None else datetime.fromisoformat(value)


class SQLiteCaseStore:
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def insert_case(self, case: ReviewCase) -> None:
        row = (
            case.case_key, case.mortgage_id, case.cycle_id, ActionFamily(case.action_family).value,
            CaseStatus(case.status).value, case.assigned_to, case.routing_policy, case.decision_reason,
            case.source_event_id, json.dumps(list(case.evidence_ids)), _ts(case.alert_received_at),
            _ts(case.case_created_at), _ts(case.ingested_at), _ts(case.scored_at), case.model_version,
            case.cluster_model_version, case.run_id, case.snapshot_id, int(case.needs_recheck), case.store,
        )
        placeholders = ", ".join("?" for _ in _CASE_COLUMNS)
        try:
            with self.conn:
                self.conn.execute(
                    f"INSERT INTO review_cases ({', '.join(_CASE_COLUMNS)}) VALUES ({placeholders})", row
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc) or "PRIMARY KEY" in str(exc):
                raise DuplicateCaseKey(case.case_key) from exc
            raise

    def get_case(self, case_key: str) -> Optional[ReviewCase]:
        row = self.conn.execute("SELECT * FROM review_cases WHERE case_key = ?", (case_key,)).fetchone()
        return None if row is None else self._to_case(row)

    def set_status(self, case_key: str, status: CaseStatus) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE review_cases SET status = ? WHERE case_key = ?", (CaseStatus(status).value, case_key)
            )

    def flag_recheck(self, case_key: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE review_cases SET needs_recheck = 1 WHERE case_key = ?", (case_key,))

    def append_audit(self, entry: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO audit_log (recorded_at, event_id, case_key, action, reason, routing_policy, details)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _ts(entry["recorded_at"]), entry["event_id"], entry.get("case_key"), entry["action"],
                    entry["reason"], entry["routing_policy"], json.dumps(entry.get("details", {}), sort_keys=True),
                ),
            )

    def append_exception(self, entry: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO exception_register (recorded_at, event_id, case_key, kind, message, attempt)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _ts(entry["recorded_at"]), entry["event_id"], entry.get("case_key"), entry["kind"],
                    entry["message"], entry.get("attempt"),
                ),
            )

    def list_cases(self) -> list[ReviewCase]:
        rows = self.conn.execute("SELECT * FROM review_cases ORDER BY case_created_at, case_key").fetchall()
        return [self._to_case(r) for r in rows]

    def list_audit(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM audit_log ORDER BY audit_id").fetchall()
        return [dict(r) | {"details": json.loads(r["details"])} for r in rows]

    def list_exceptions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM exception_register ORDER BY exception_id").fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _to_case(row: sqlite3.Row) -> ReviewCase:
        return ReviewCase(
            case_key=row["case_key"],
            mortgage_id=row["mortgage_id"],
            cycle_id=row["cycle_id"],
            action_family=ActionFamily(row["action_family"]),
            status=CaseStatus(row["status"]),
            assigned_to=row["assigned_to"],
            routing_policy=row["routing_policy"],
            decision_reason=row["decision_reason"],
            source_event_id=row["source_event_id"],
            evidence_ids=tuple(json.loads(row["evidence_ids"])),
            alert_received_at=_from_ts(row["alert_received_at"]),
            case_created_at=_from_ts(row["case_created_at"]),
            ingested_at=_from_ts(row["ingested_at"]),
            scored_at=_from_ts(row["scored_at"]),
            model_version=row["model_version"],
            cluster_model_version=row["cluster_model_version"],
            run_id=row["run_id"],
            snapshot_id=row["snapshot_id"],
            needs_recheck=bool(row["needs_recheck"]),
            store=row["store"],
        )
