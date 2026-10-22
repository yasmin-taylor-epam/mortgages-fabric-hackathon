"""Per-account feature attributions (blueprint section 04, Explanations).

- Logistic regression: coefficient x standardised value. Units: log-odds
  relative to the average training account.
- Decision tree: SHAP values from TreeExplainer. Units: probability.
- Rules baseline: 0.25 for each rule that fires.

Attributions explain the score. They do not prove the borrower's motive.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.tree import DecisionTreeClassifier

from .models import RulesBaseline

UNITS = {"logistic_regression": "log_odds", "decision_tree": "probability_shap", "rules_baseline": "rule_weight"}


def contributions(model, X: pd.DataFrame) -> pd.DataFrame:
    """One column per feature, one row per account."""
    if isinstance(model, Pipeline):
        z = model.named_steps["scale"].transform(X)
        coef = model.named_steps["clf"].coef_[0]
        return pd.DataFrame(z * coef, index=X.index, columns=X.columns)
    if isinstance(model, DecisionTreeClassifier):
        import shap

        values = shap.TreeExplainer(model).shap_values(X.to_numpy())
        arr = np.asarray(values)
        if isinstance(values, list):
            arr = np.asarray(values[1])
        elif arr.ndim == 3:
            arr = arr[:, :, 1]
        return pd.DataFrame(arr, index=X.index, columns=X.columns)
    if isinstance(model, RulesBaseline):
        flags = model.rule_flags(X) / len(model.RULES)
        source = {"failed_attempt_30d": "pt_failed_attempts_30d", "unrecovered_software_error": "pt_unrecovered_software_error",
                  "stalled_stage": "pt_days_at_current_stage", "abandoned_or_rejected": "pt_abandoned_or_rejected_count"}
        out = pd.DataFrame(0.0, index=X.index, columns=X.columns)
        for rule, col in source.items():
            out[col] = flags[rule]
        return out
    raise TypeError(f"No attribution method for {type(model).__name__}.")


def to_long(contrib: pd.DataFrame, X: pd.DataFrame, keys: pd.DataFrame, top_n: int | None = None) -> pd.DataFrame:
    """Long table: one row per account and feature. rank 1 = largest absolute contribution."""
    c = contrib.copy()
    c.index = range(len(c))
    x = X.copy()
    x.index = range(len(x))
    long = c.stack().rename("contribution").reset_index().rename(columns={"level_0": "_row", "level_1": "feature_name"})
    long["feature_value"] = x.stack().to_numpy()
    long["_abs"] = long["contribution"].abs()
    long = long.sort_values(["_row", "_abs", "feature_name"], ascending=[True, False, True])
    long["rank"] = long.groupby("_row").cumcount() + 1
    if top_n:
        long = long[long["rank"] <= top_n]
    k = keys.reset_index(drop=True)
    long = long.join(k, on="_row")
    return long.drop(columns=["_row", "_abs"]).reset_index(drop=True)
