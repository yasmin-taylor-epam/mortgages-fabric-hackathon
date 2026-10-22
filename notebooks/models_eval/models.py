"""Rules baseline and classifiers (blueprint section 04).

- Rules baseline: product-transfer request friction. Each rule adds 0.25.
- Logistic regression on standardised features.
- Shallow decision tree (depth 3 or 4).

Hyperparameters, the baseline stall rule and the alert thresholds are frozen
on validation data. The test cohorts never influence them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, f1_score, log_loss, precision_score, recall_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier


class RulesBaseline(ClassifierMixin, BaseEstimator):
    """Product-transfer request friction rules. Score = share of rules that fire."""

    RULES = ("failed_attempt_30d", "unrecovered_software_error", "stalled_stage", "abandoned_or_rejected")

    def __init__(self, stall_days: float = 21.0):
        self.stall_days = stall_days

    def fit(self, X: pd.DataFrame, y=None):
        self.classes_ = np.array([0, 1])
        self.feature_names_in_ = np.array(list(X.columns))
        return self

    def rule_flags(self, X: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({
            "failed_attempt_30d": X["pt_failed_attempts_30d"] > 0,
            "unrecovered_software_error": X["pt_unrecovered_software_error"] > 0,
            "stalled_stage": X["pt_days_at_current_stage"] >= self.stall_days,
            "abandoned_or_rejected": X["pt_abandoned_or_rejected_count"] > 0,
        }, index=X.index).astype(float)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        s = self.rule_flags(X).mean(axis=1).to_numpy()
        return np.column_stack([1 - s, s])

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] > 0).astype(int)


def make_logreg(C: float = 1.0) -> Pipeline:
    return Pipeline([("scale", StandardScaler()), ("clf", LogisticRegression(C=C, max_iter=2000))])


def make_tree(depth: int = 3, min_leaf: int = 25, seed: int = 0) -> DecisionTreeClassifier:
    return DecisionTreeClassifier(max_depth=depth, min_samples_leaf=min_leaf, random_state=seed)


def proba(model, X: pd.DataFrame) -> np.ndarray:
    return model.predict_proba(X)[:, 1]


def _safe_log_loss(y, p) -> float:
    return float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1]))


def tune(X_tr, y_tr, X_va, y_va, seed: int = 0) -> dict:
    """Fit each candidate on train. Pick settings by validation results only."""
    out = {}
    stall = []
    for d in (14, 21, 30, 45):
        m = RulesBaseline(stall_days=d).fit(X_tr, y_tr)
        stall.append((average_precision_score(y_va, proba(m, X_va)), -d, d, m))
    ap, _, d, base = max(stall, key=lambda t: (t[0], t[1]))
    out["rules_baseline"] = {"model": base, "params": {"stall_days": d}, "validation_ap": float(ap)}

    lr = []
    for C in (0.03, 0.1, 0.3, 1.0):
        m = make_logreg(C).fit(X_tr, y_tr)
        lr.append((_safe_log_loss(y_va, proba(m, X_va)), C, m))
    ll, C, m = min(lr, key=lambda t: (t[0], t[1]))
    out["logistic_regression"] = {"model": m, "params": {"C": C}, "validation_log_loss": ll,
                                  "validation_ap": float(average_precision_score(y_va, proba(m, X_va)))}

    tr = []
    for depth in (3, 4):
        m = make_tree(depth, seed=seed).fit(X_tr, y_tr)
        tr.append((_safe_log_loss(y_va, proba(m, X_va)), depth, m))
    ll, depth, m = min(tr, key=lambda t: (t[0], t[1]))
    out["decision_tree"] = {"model": m, "params": {"max_depth": depth, "min_samples_leaf": 25}, "validation_log_loss": ll,
                            "validation_ap": float(average_precision_score(y_va, proba(m, X_va)))}
    return out


def freeze_threshold(y, p) -> dict:
    """Alert threshold that maximises F1 on validation data. Ties keep the higher threshold."""
    y, p = np.asarray(y), np.asarray(p)
    best = None
    for t in np.unique(np.round(p, 6)):
        pred = (p >= t).astype(int)
        if pred.sum() == 0 or pred.all():
            continue  # alerting nobody or everybody is not a threshold
        f1 = f1_score(y, pred, zero_division=0)
        cand = (round(float(f1), 6), float(t))
        if best is None or cand > best:
            best = cand
    t = best[1] if best else 0.5
    pred = (p >= t).astype(int)
    return {"threshold": round(t, 4), "rule": "max F1 on validation", "validation_f1": round(float(f1_score(y, pred, zero_division=0)), 4),
            "validation_precision": round(float(precision_score(y, pred, zero_division=0)), 4),
            "validation_recall": round(float(recall_score(y, pred, zero_division=0)), 4),
            "validation_alert_rate": round(float(pred.mean()), 4)}


def choose_champion(tuned: dict, margin: float = 0.005) -> str:
    """Highest validation average precision among the learned models.

    Logistic regression wins a near tie because it is simpler and better calibrated.
    """
    lr, tree = tuned["logistic_regression"]["validation_ap"], tuned["decision_tree"]["validation_ap"]
    return "decision_tree" if tree > lr + margin else "logistic_regression"
