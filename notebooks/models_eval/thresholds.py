"""Alert threshold rules, and the table that compares them.

The threshold is not typed in. Training calls `freeze` with a named rule (a setting), and the release stores the
rule name, its parameters and the value. The routing policy and notebook 04 read the value from the release.

Rules:
- max_f1   the threshold with the best F1 on validation data. The default.
- min_cost the threshold with the lowest total cost on validation data: cost_missed_leaver x missed leavers +
           cost_review x alerts. Both costs are required. No default cost is invented.
"""
from __future__ import annotations

import math

import numpy as np
from sklearn.metrics import f1_score, fbeta_score, precision_score, recall_score

RULE_TEXT = {"max_f1": "max F1 on validation", "min_cost": "lowest cost on validation"}


def _arrays(y, p):
    return np.asarray(y).astype(int), np.asarray(p, dtype=float)


def _cuts(p: np.ndarray) -> np.ndarray:
    return np.unique(np.round(p, 6))


def _usable(pred: np.ndarray) -> bool:
    return bool(pred.sum()) and not bool(pred.all())  # alerting nobody or everybody is not a threshold


def choose(y, p, rule: str = "max_f1", cost_missed_leaver: float | None = None, cost_review: float | None = None) -> float | None:
    """The threshold for a rule. Ties keep the higher threshold. None when no threshold is usable."""
    y, p = _arrays(y, p)
    if rule == "min_cost":
        if cost_missed_leaver is None or cost_review is None:
            raise ValueError("The min_cost rule needs cost_missed_leaver and cost_review. No default cost is assumed.")
        if cost_missed_leaver <= 0 or cost_review < 0:
            raise ValueError("cost_missed_leaver must be above 0 and cost_review must not be negative.")
    elif rule != "max_f1":
        raise ValueError(f"Unknown threshold rule {rule!r}. Known: {sorted(RULE_TEXT)}.")
    best = None
    for t in _cuts(p):
        pred = p >= t
        if not _usable(pred):
            continue
        if rule == "max_f1":
            key = (round(float(f1_score(y, pred.astype(int), zero_division=0)), 6), float(t))
        else:
            missed = int(((~pred) & (y == 1)).sum())
            key = (-(cost_missed_leaver * missed + cost_review * int(pred.sum())), float(t))  # a lower cost is a higher key
        if best is None or key > best:
            best = key
    return None if best is None else best[1]


def freeze(y, p, rule: str = "max_f1", cost_missed_leaver: float | None = None, cost_review: float | None = None) -> dict:
    """The frozen threshold and its validation numbers, with the rule that chose it."""
    y, p = _arrays(y, p)
    t = choose(y, p, rule, cost_missed_leaver, cost_review)
    t = 0.5 if t is None else t
    pred = (p >= t).astype(int)
    params = {"cost_missed_leaver": cost_missed_leaver, "cost_review": cost_review} if rule == "min_cost" else {}
    return {"threshold": round(t, 4), "rule": RULE_TEXT[rule], "rule_name": rule, "rule_params": params,
            "validation_f1": round(float(f1_score(y, pred, zero_division=0)), 4),
            "validation_precision": round(float(precision_score(y, pred, zero_division=0)), 4),
            "validation_recall": round(float(recall_score(y, pred, zero_division=0)), 4),
            "validation_alert_rate": round(float(pred.mean()), 4)}


def _row(y, p, t) -> dict:
    pred = p >= t
    alerts, found = int(pred.sum()), int((pred & (y == 1)).sum())
    return {"alerts": alerts, "leavers_found": found, "alert_share": round(alerts / len(p), 4) if len(p) else None,
            "recall": round(found / int(y.sum()), 4) if int(y.sum()) else None,
            "precision": round(found / alerts, 4) if alerts else None}


def _top_share(p: np.ndarray, share: float) -> float:
    order = np.sort(p)[::-1]
    return float(order[max(math.ceil(share * len(order)), 1) - 1])


def _recall_target(y, p, target: float) -> float:
    for t in sorted(_cuts(p), reverse=True):
        pred = p >= t
        if int(y.sum()) and int((pred & (y == 1)).sum()) / int(y.sum()) >= target:
            return float(t)
    return float(_cuts(p).min())


def options(y_val, p_val, y_test, p_test, chosen: float, chosen_rule: str, open_scores=None, retired: float | None = None) -> list[dict]:
    """Compare thresholds on validation and test data. For reading only: nothing here sets the routing threshold.

    `retired` is the old "10th-highest validation score" number. It is shown for comparison and labelled retired.
    """
    yv, pv = _arrays(y_val, p_val)
    yt, pt = _arrays(y_test, p_test)
    f2 = max(((float(fbeta_score(yv, (pv >= t).astype(int), beta=2, zero_division=0)), float(t)) for t in _cuts(pv) if _usable(pv >= t)), default=(0, 0.5))[1]
    rows = [(f"chosen rule ({chosen_rule})", chosen), ("max F2 on validation (recall counts twice)", f2),
            ("80% recall on validation", _recall_target(yv, pv, 0.80)), ("top 10% of validation scores", _top_share(pv, 0.10)),
            ("top 5% of validation scores", _top_share(pv, 0.05))]
    if retired is not None:
        rows.append(("retired: 10th-highest validation score (for comparison only)", float(retired)))
    out = []
    for name, t in rows:
        row = {"option": name, "threshold": round(float(t), 4), "validation": _row(yv, pv, t), "test": _row(yt, pt, t)}
        if open_scores is not None and len(open_scores):
            s = np.asarray(open_scores, dtype=float)
            row["open_accounts"] = {"accounts": int(len(s)), "alerts": int((s >= t).sum())}
        out.append(row)
    return out
