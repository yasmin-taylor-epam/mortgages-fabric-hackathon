"""Put together what each page shows. Every value comes from the source at the time of the request."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import reasons
from .config import REPO, Settings
from .events import next_demo_time
from .sources import arrival_delay, check_mortgage_id, is_live

OPEN = ("open", "on_hold")


def ts(value) -> pd.Timestamp | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    t = pd.Timestamp(value)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def day(value) -> str:
    t = ts(value)
    return t.strftime("%d %b %Y") if t is not None else "-"


def when(value) -> str:
    t = ts(value)
    return t.strftime("%d %b %Y %H:%M") if t is not None else "-"


def on_demo_clock(value, today: pd.Timestamp) -> bool:
    """True when a time belongs to the story (on or before the demo 'today'). Times written by Fabric jobs and by the
    replay carry the real date (November 2026 or later), which is after the story, so they are not shown as story times."""
    t = ts(value)
    return t is not None and t <= today + pd.Timedelta(days=1)


def _payload(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}


@dataclass
class Problem:
    """A query that failed. The page shows it in plain words instead of crashing."""
    what: str
    error: str


@dataclass
class Board:
    settings: Settings
    source: Any
    problems: list[Problem] = field(default_factory=list)
    _policy: dict | None = field(default=None, repr=False)

    # ----- helpers
    def _try(self, what: str, fn, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - shown on the page
            self.problems.append(Problem(what, str(exc)[:300]))
            return default

    def policy(self) -> dict:
        if self._policy is None:
            from review_routing import route as rr
            self._policy = rr.load_policy(self.settings.policy_path, REPO / "data" / "output")[0]
        return self._policy

    def threshold(self) -> float:
        return float(self.policy()["risk_threshold"])

    def name(self, mortgage_id: str) -> str:
        for p in self.settings.personas:
            if p.mortgage_id == mortgage_id:
                return p.name
        return mortgage_id

    def latest_run(self) -> dict | None:
        rows = self._try("latest scoring run (Lakehouse table score_runs)", lambda: self.source.sql(
            "SELECT run_id, run_kind, status, started_at, completed_at, cutoff_at, snapshot_count FROM score_runs WHERE status = 'completed'"), [])
        return max(rows, key=lambda r: (str(r["completed_at"]), r["run_id"])) if rows else None

    def cases(self, mortgage_id: str | None = None) -> list[dict]:
        q, p = "SELECT * FROM review_cases", ()
        if mortgage_id:
            q, p = q + " WHERE mortgage_id = ?", (mortgage_id,)
        return self._try("review cases (Lakehouse table review_cases, written by notebook 07)", lambda: self.source.sql(q, p), [])

    def counts(self) -> dict:
        return self._try("event counts (Eventhouse tables RawEvents and QuarantinedEvents)", self.source.event_counts, {})

    def as_of(self, counts: dict | None = None) -> pd.Timestamp:
        """The demo 'today'. It is the time of the latest portal event, or the demo start. It is not the real clock."""
        counts = counts if counts is not None else self.counts()
        last = counts.get("last_live_occurred")
        start = next_demo_time(self.settings, None)
        return max(start, ts(last)) if last else start

    # ----- staff worklist
    def worklist(self) -> dict:
        run = self.latest_run()
        counts = self.counts()
        today = self.as_of(counts)
        all_cases = self.cases()
        renewal_cases = {c["account_key"]: c for c in all_cases if c.get("action_family") == "renewal"}
        renewals = []
        if run:
            ranked = self._try("ranked accounts (Lakehouse tables account_scores and mortgage_account)", lambda: self.source.sql(
                "SELECT s.account_key, s.mortgage_id, s.score, s.priority_rank, s.segment_id, a.owner_id, a.deal_end_at, a.balance "
                "FROM account_scores s JOIN mortgage_account a ON a.account_key = s.account_key "
                "WHERE s.run_id = ? AND s.priority_rank IS NOT NULL", (run["run_id"],)), [])
            attributions = self._try("reasons (Lakehouse table feature_attributions)", lambda: self.source.sql(
                "SELECT f.account_key, f.feature_name, f.feature_value, f.contribution FROM feature_attributions f "
                "JOIN account_scores s ON s.account_key = f.account_key AND s.run_id = f.run_id "
                "WHERE f.run_id = ? AND s.priority_rank IS NOT NULL AND f.contribution > 0", (run["run_id"],)), [])
            by_account: dict[str, list[dict]] = {}
            for a in attributions:
                by_account.setdefault(a["account_key"], []).append(a)
            for r in sorted(ranked, key=lambda r: r["priority_rank"]):
                case = renewal_cases.get(r["account_key"])
                end = ts(r["deal_end_at"])
                renewals.append({
                    **r, "name": self.name(r["mortgage_id"]), "reason": reasons.top_reason(by_account.get(r["account_key"], [])),
                    "deal_end": day(end), "days_left": (end - today).days if end is not None else None,
                    "case": case, "case_status": reasons.CASE_STATUSES.get(case["status"], case["status"]) if case else "No case yet",
                    "owner": (case or {}).get("assigned_to") or r.get("owner_id") or "-",
                })
        help_cases = [c for c in all_cases if c.get("action_family") in ("support", "technical") and c.get("status") in OPEN]
        help_cases.sort(key=lambda c: str(c.get("case_created_at") or ""), reverse=True)
        support = [self._case_row(c, today, run) for c in help_cases if c["action_family"] == "support"]
        technical = [self._case_row(c, today, run) for c in help_cases if c["action_family"] == "technical"]
        return {"run": run, "today": today, "renewals": renewals, "support": support, "technical": technical[:8],
                "technical_total": len(technical), "threshold": self.threshold(), "counts": counts}

    def _case_row(self, c: dict, today: pd.Timestamp, run: dict | None) -> dict:
        created = c.get("case_created_at")
        if on_demo_clock(created, today):
            opened = when(created)
        else:  # a case from the scoring run: notebook 07 stamps it with the real run time
            opened = f"Scoring run (data to {day(run['cutoff_at'])})" if run else "Scoring run"
        return {**c, "name": self.name(c["mortgage_id"]), "family_text": reasons.CASE_FAMILIES.get(c["action_family"], c["action_family"]),
                "opened": opened, "status_text": reasons.CASE_STATUSES.get(c["status"], c["status"]),
                "live": is_live(c.get("source_event_id") or "")}

    def _arrival(self, e: dict, today: pd.Timestamp) -> str:
        """When the event reached the Eventhouse, on the demo clock. Empty when that is not known."""
        log = getattr(self.source, "sent_log", None)
        delay = arrival_delay(log.get(e["event_id"]) if log else None, e.get("ingested_at"))
        if delay is not None and ts(e.get("occurred_at")) is not None:
            return f"arrived {when(ts(e['occurred_at']) + pd.Timedelta(seconds=delay))}, {delay} s after sending"
        if on_demo_clock(e.get("ingested_at"), today):
            return f"arrived {when(e.get('ingested_at'))}"
        return ""  # replayed history: Fabric got it in one batch on the real clock

    # ----- staff customer page
    def customer(self, mortgage_id: str) -> dict:
        mid = check_mortgage_id(mortgage_id)
        run = self.latest_run()
        counts = self.counts()
        today = self.as_of(counts)
        acct = self._try("deal (Lakehouse tables mortgage and mortgage_account)", lambda: self.source.sql(
            "SELECT a.* FROM mortgage m JOIN mortgage_account a ON a.account_key = m.current_account_key WHERE m.mortgage_id = ?", (mid,)), [])
        acct = acct[0] if acct else None
        score, rank_by_score, scored, factors = None, None, 0, []
        if run:
            rows = self._try("score (Lakehouse table account_scores)", lambda: self.source.sql(
                "SELECT * FROM account_scores WHERE run_id = ? AND mortgage_id = ?", (run["run_id"], mid)), [])
            score = rows[0] if rows else None
            if score:
                stat = self._try("rank by score (Lakehouse table account_scores)", lambda: self.source.sql(
                    "SELECT COUNT(*) AS scored, SUM(CASE WHEN score > ? THEN 1 ELSE 0 END) AS higher FROM account_scores WHERE run_id = ?",
                    (score["score"], run["run_id"])), [])
                if stat:
                    scored, rank_by_score = int(stat[0]["scored"] or 0), int(stat[0]["higher"] or 0) + 1
                f_rows = self._try("reasons (Lakehouse table feature_attributions)", lambda: self.source.sql(
                    "SELECT feature_name, feature_value, contribution, rank FROM feature_attributions WHERE run_id = ? AND mortgage_id = ?",
                    (run["run_id"], mid)), [])
                for f in sorted(f_rows, key=lambda f: f["rank"])[:8]:
                    factors.append({**f, "text": reasons.describe(f["feature_name"], f["feature_value"]),
                                    "direction": reasons.direction(f["contribution"])})
        timeline = []
        for e in self._try("event timeline (Eventhouse table RawEvents)", lambda: self.source.raw_events(mid), []):
            p = _payload(e.get("payload"))
            timeline.append({**e, "when": when(e.get("occurred_at")), "arrived": self._arrival(e, today),
                             "kind": reasons.EVENT_TYPES.get(e["event_type"], e["event_type"]),
                             "text": reasons.event_text(e["event_type"], p), "live": is_live(e["event_id"]),
                             "fault": p.get("status") == "error"})
        cases = [self._case_row(c, today, run) for c in self.cases(mid)]
        decisions = self._try("routing decisions (Lakehouse table review_decisions)", lambda: self.source.sql(
            "SELECT * FROM review_decisions WHERE mortgage_id = ?", (mid,)), [])
        happened = {e["event_id"]: e.get("occurred_at") for e in timeline}
        for d in decisions:
            at = happened.get(d.get("event_id")) or (d.get("arrived_at") if on_demo_clock(d.get("arrived_at"), today) else None)
            d["when"] = when(at) if at else (f"Scoring run (data to {day(run['cutoff_at'])})" if run else "-")
            d["sort"] = str(at or "")
        decisions = sorted(decisions, key=lambda d: d["sort"], reverse=True)[:10]
        servicing = self._try("service history (Lakehouse table servicing_case)", lambda: self.source.sql(
            "SELECT case_id, category, support_route, opened_at, latest_status, is_resolved, contact_count FROM servicing_case WHERE mortgage_id = ?",
            (mid,)), [])
        end = ts(acct["deal_end_at"]) if acct else None
        return {"mortgage_id": mid, "name": self.name(mid), "acct": acct, "run": run, "score": score, "scored": scored,
                "rank_by_score": rank_by_score, "threshold": self.threshold(), "factors": factors, "timeline": timeline,
                "cases": cases, "decisions": decisions, "servicing": sorted(servicing, key=lambda s: str(s["opened_at"]), reverse=True),
                "today": today, "deal_end": day(end), "days_left": (end - today).days if end is not None else None}

    # ----- customer portal
    def portal_home(self, mortgage_id: str) -> dict:
        mid = check_mortgage_id(mortgage_id)
        acct = self._try("deal (Lakehouse tables mortgage and mortgage_account)", lambda: self.source.sql(
            "SELECT a.* FROM mortgage m JOIN mortgage_account a ON a.account_key = m.current_account_key WHERE m.mortgage_id = ?", (mid,)), [])
        steps = [e for e in self._try("renewal steps (Eventhouse table RawEvents)", lambda: self.source.raw_events(mid), [])
                 if e["event_type"] == "transfer_request_event"]
        latest = None
        if steps:
            e = max(steps, key=lambda e: str(e["occurred_at"]))
            p = _payload(e.get("payload"))
            latest = {"application_id": e.get("application_id"), "stage": p.get("stage"), "status": p.get("status"),
                      "occurred_at": e["occurred_at"], "source": "Eventhouse RawEvents"}
        else:
            rows = self._try("renewal steps (Lakehouse table transfer_request_event)", lambda: self.source.sql(
                "SELECT application_id, stage, status, occurred_at FROM transfer_request_event WHERE mortgage_id = ?", (mid,)), [])
            if rows:
                latest = {**max(rows, key=lambda r: str(r["occurred_at"])), "source": "Lakehouse transfer_request_event"}
        if latest and not latest.get("application_id"):
            app_rows = self._try("renewal application (Lakehouse table transfer_request_event)", lambda: self.source.sql(
                "SELECT application_id, occurred_at FROM transfer_request_event WHERE mortgage_id = ?", (mid,)), [])
            latest["application_id"] = max(app_rows, key=lambda r: str(r["occurred_at"]))["application_id"] if app_rows else None
        a = acct[0] if acct else None
        today = self.as_of()
        end = ts(a["deal_end_at"]) if a else None
        return {"mortgage_id": mid, "name": self.name(mid), "acct": a, "latest": latest,
                "stage_text": reasons.STAGES.get((latest or {}).get("stage") or "", "not started"),
                "deal_end": day(end), "days_left": (end - today).days if end is not None else None, "today": today}

    # ----- staff summary
    def summary(self) -> dict:
        run = self.latest_run()
        threshold = self.threshold()
        counts = self.counts()
        scores = {}
        segments = []
        if run:
            rows = self._try("score counts (Lakehouse table account_scores)", lambda: self.source.sql(
                "SELECT COUNT(*) AS scored, SUM(CASE WHEN score >= ? THEN 1 ELSE 0 END) AS above, "
                "SUM(CASE WHEN priority_rank IS NOT NULL THEN 1 ELSE 0 END) AS ranked FROM account_scores WHERE run_id = ?",
                (threshold, run["run_id"])), [])
            scores = rows[0] if rows else {}
            segments = self._try("segments (Lakehouse table segment_membership)", lambda: self.source.sql(
                "SELECT segment_id, COUNT(*) AS n FROM segment_membership WHERE run_id = ? GROUP BY segment_id", (run["run_id"],)), [])
        accounts = self._try("accounts (Lakehouse table mortgage_account)", lambda: self.source.sql(
            "SELECT COUNT(*) AS n FROM mortgage_account"), [{"n": None}])
        cases = self.cases()
        by_family: dict[str, int] = {}
        for c in cases:
            if c.get("status") in OPEN:
                by_family[c["action_family"]] = by_family.get(c["action_family"], 0) + 1
        runs = self._try("routing runs (Lakehouse table review_routing_runs)", lambda: self.source.sql(
            "SELECT routed_at, score_run_id, service_events, cases, rejected_events, exceptions FROM review_routing_runs"), [])
        last_routing = max(runs, key=lambda r: str(r["routed_at"])) if runs else None
        return {"run": run, "threshold": threshold, "scores": scores, "segments": sorted(segments, key=lambda s: s["segment_id"]),
                "accounts": accounts[0]["n"] if accounts else None, "open_by_family": by_family, "counts": counts,
                "last_routing": last_routing, "today": self.as_of(counts)}
