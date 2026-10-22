"""Later-cohort evaluation (notebook 05_evaluate, blueprint section 04).

Each model is tested on the later deal cohorts. Their borrowers are not in
training. Thresholds come from the release; the test data never sets them.
Reported: accuracy, precision, recall, calibration, and lift at ten reviews per
run. One checkpoint run scores one deal-end cohort, so lift at ten is computed
per test cohort.

Synthetic results show that the workflow works. They do not show real
predictive power or improved retention.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.metrics import (accuracy_score, average_precision_score, brier_score_loss, f1_score, log_loss,
                             precision_score, recall_score, roc_auc_score)

from . import clusters, features, models, registry, train
from .config import REVIEWS_PER_RUN, Paths, SplitConfig

PROXY_COLUMNS = ["ctx_payment_support_open", "ctx_missed_payments_180d", "balance"]
PROXY_FLAG = 0.3


def expected_positives_at_k(y, p, k: int) -> float:
    """Positives among the k highest scores. Ties at the cut share the remaining places evenly."""
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    k = min(k, len(p))
    if k == 0:
        return 0.0
    cut = np.sort(p)[::-1][k - 1]
    above = p > cut
    tied = p == cut
    room = k - int(above.sum())
    return float(y[above].sum() + room * y[tied].mean())


def lift_at_k(y, p, k: int = REVIEWS_PER_RUN) -> dict:
    y = np.asarray(y, dtype=float)
    base = float(y.mean()) if len(y) else float("nan")
    k_eff = min(k, len(y))
    prec = expected_positives_at_k(y, p, k_eff) / k_eff if k_eff else float("nan")
    return {"k": k_eff, "precision_at_k": round(prec, 4), "base_rate": round(base, 4),
            "lift": round(prec / base, 3) if base > 0 else None}


def lift_interval(y, p, k: int = REVIEWS_PER_RUN, n_boot: int = 500, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    vals = []
    for _ in range(n_boot):
        i = rng.integers(0, len(y), len(y))
        r = lift_at_k(y[i], p[i], k)
        if r["lift"] is not None:
            vals.append(r["lift"])
    return (round(float(np.percentile(vals, 2.5)), 3), round(float(np.percentile(vals, 97.5)), 3)) if vals else (None, None)


def calibration(y, p, bins: int = 10) -> dict:
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    q = pd.qcut(pd.Series(p).rank(method="first"), q=min(bins, len(p)), labels=False)
    t = pd.DataFrame({"bin": q, "p": p, "y": y}).groupby("bin").agg(n=("y", "size"), mean_score=("p", "mean"), observed_rate=("y", "mean"))
    ece = float((t["n"] * (t["mean_score"] - t["observed_rate"]).abs()).sum() / t["n"].sum())
    return {"expected_calibration_error": round(ece, 4), "brier": round(float(brier_score_loss(y, p)), 4),
            "bins": t.round(4).reset_index().to_dict("records")}


def classification(y, p, threshold: float) -> dict:
    pred = (np.asarray(p) >= threshold).astype(int)
    return {"threshold": threshold, "alerts": int(pred.sum()), "accounts": int(len(pred)),
            "accuracy": round(float(accuracy_score(y, pred)), 4),
            "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
            "recall": round(float(recall_score(y, pred, zero_division=0)), 4),
            "f1": round(float(f1_score(y, pred, zero_division=0)), 4),
            "majority_class_accuracy": round(float(max(np.mean(y), 1 - np.mean(y))), 4)}


def ranking(y, p) -> dict:
    return {"roc_auc": round(float(roc_auc_score(y, p)), 4), "average_precision": round(float(average_precision_score(y, p)), 4),
            "log_loss": round(float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1])), 4)}


def proxy_check(frame: pd.DataFrame, X: pd.DataFrame) -> list[dict]:
    """Correlation of each model input with sensitive or exposure context that the model does not use."""
    out = []
    for col in PROXY_COLUMNS:
        if col not in frame:
            continue
        s = pd.to_numeric(frame[col].astype(float), errors="coerce")
        ok = s.notna()
        if ok.sum() < 10 or s[ok].std() == 0:
            continue
        for f in X.columns:
            v = X.loc[ok.values, f]
            if v.std() == 0:
                continue
            r = float(np.corrcoef(v, s[ok])[0, 1])
            if abs(r) >= 0.1:
                out.append({"feature": f, "context": col, "correlation": round(r, 3), "flag": abs(r) >= PROXY_FLAG})
    return sorted(out, key=lambda d: -abs(d["correlation"]))


def evaluate_release(paths: Paths, model_version: str | None = None) -> dict:
    loaded = registry.load_release(paths, model_version)
    rel = loaded["release"]
    keys = ("train_cohorts", "validation_cohorts", "test_cohorts", "release_at", "seed")
    cfg = SplitConfig(**{k: tuple(v) if isinstance(v, list) else v for k, v in rel["split"].items() if k in keys})
    data = train.prepare(paths, cfg)
    test, X, y = data["parts"]["test"], data["X"]["test"], data["y"]["test"]

    out = {"release_id": rel["release_id"], "model_version": rel["model_version"],
           "cluster_model_version": rel["cluster_model_version"], "champion": rel["champion"], "synthetic_data": True,
           "alert_threshold": rel["alert_threshold"], "review_capacity_threshold": rel.get("review_capacity_threshold"),
           "test_cohorts": list(cfg.test_cohorts), "test_accounts": int(len(test)), "test_base_rate": round(float(y.mean()), 4),
           "reviews_per_run": REVIEWS_PER_RUN, "models": {}}
    for name, model in loaded["candidates"].items():
        p = models.proba(model, X)
        thr = rel["validation"][name]["threshold"]
        per_cohort = []
        for cohort, idx in test.groupby("cohort_id").groups.items():
            pos = test.index.get_indexer(idx)
            lk = lift_at_k(y[pos], p[pos])
            lo, hi = lift_interval(y[pos], p[pos], seed=cfg.seed % 1000)
            per_cohort.append({"cohort_id": cohort, "accounts": int(len(pos)), **lk, "lift_95_interval": [lo, hi]})
        lifts = [c["lift"] for c in per_cohort if c["lift"] is not None]
        out["models"][name] = {"role": "champion" if name == rel["champion"] else ("baseline" if name == "rules_baseline" else "challenger"),
                               "params": rel["params"][name], **classification(y, p, thr), **ranking(y, p),
                               "calibration": calibration(y, p), "lift_at_10_by_cohort": per_cohort,
                               "mean_lift_at_10": round(float(np.mean(lifts)), 3) if lifts else None}
    base = out["models"]["rules_baseline"]
    for name, m in out["models"].items():
        m["vs_baseline"] = {"average_precision_gain": round(m["average_precision"] - base["average_precision"], 4),
                            "mean_lift_at_10_gain": (round(m["mean_lift_at_10"] - base["mean_lift_at_10"], 3)
                                                     if m["mean_lift_at_10"] is not None and base["mean_lift_at_10"] is not None else None)}

    seg = clusters.assign(loaded["segment_model"], loaded["segment_map"], test)
    out["segments"] = {"k": rel["segments"]["k"], "selection": rel["segments"]["selection"],
                       "train_profiles": rel["segments"]["profiles"],
                       "test_profiles": clusters.profiles(test, seg, test["label"]).to_dict("records")}
    out["proxy_check"] = {"threshold": PROXY_FLAG, "columns": PROXY_COLUMNS, "results": proxy_check(test, X)}
    out["split"] = rel["split"]
    return out


def _fmt(v) -> str:
    return "n/a" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def to_markdown(r: dict) -> str:
    lines = [
        "# HomeBridge refinancing risk: later-cohort evaluation",
        "",
        "**Synthetic data.** HomeBridge is a fictional lender. These results show that the workflow works. "
        "They do not show real predictive power or improved retention.",
        "",
        f"- Release: `{r['release_id']}`. Classifier `{r['model_version']}` ({r['champion']}). Clusters `{r['cluster_model_version']}`.",
        f"- Simulated release date: {r['split']['release_at']}. Train cohorts {', '.join(r['split']['train_cohorts'])}. "
        f"Validation {', '.join(r['split']['validation_cohorts'])}. Test {', '.join(r['test_cohorts'])}.",
        f"- Test accounts: {r['test_accounts']}. Refinancing base rate: {_fmt(r['test_base_rate'])}.",
        f"- Thresholds were frozen on validation data. Accounts shared with test cohorts were removed from training: "
        f"{r['split']['dropped_shared_borrower']}.",
        "",
        "## Model comparison on the test cohorts",
        "",
        "| Model | Role | Threshold | Accuracy | Precision | Recall | ROC AUC | Avg precision | Brier | ECE | Mean lift at 10 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, m in r["models"].items():
        lines.append(f"| {name} | {m['role']} | {_fmt(m['threshold'])} | {_fmt(m['accuracy'])} | {_fmt(m['precision'])} | "
                     f"{_fmt(m['recall'])} | {_fmt(m['roc_auc'])} | {_fmt(m['average_precision'])} | {_fmt(m['calibration']['brier'])} | "
                     f"{_fmt(m['calibration']['expected_calibration_error'])} | {_fmt(m['mean_lift_at_10'])} |")
    lines += ["", "Accuracy alone misleads when few accounts leave. Majority-class accuracy on the test cohorts is "
              f"{_fmt(next(iter(r['models'].values()))['majority_class_accuracy'])}.", "",
              f"Alert threshold candidates for the champion, frozen on validation data: max-F1 threshold {_fmt(r.get('alert_threshold'))}; "
              f"ten-review capacity threshold {_fmt(r.get('review_capacity_threshold'))}. The team confirms the routing threshold.", "",
              "## Lift at ten reviews per run", "",
              "Lift = refinancing rate among the ten highest-scored accounts ÷ the cohort base rate. Ten accounts is a small "
              "sample, so the 95% bootstrap interval is wide.", "",
              "| Model | Cohort | Accounts | Precision at 10 | Base rate | Lift | 95% interval |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, m in r["models"].items():
        for c in m["lift_at_10_by_cohort"]:
            lines.append(f"| {name} | {c['cohort_id']} | {c['accounts']} | {_fmt(c['precision_at_k'])} | {_fmt(c['base_rate'])} | "
                         f"{_fmt(c['lift'])} | {c['lift_95_interval']} |")
    champ = r["models"][r["champion"]]
    lines += ["", f"## Calibration of the champion ({r['champion']})", "",
              "| Bin | Accounts | Mean score | Observed rate |", "| --- | --- | --- | --- |"]
    for b in champ["calibration"]["bins"]:
        lines.append(f"| {int(b['bin']) + 1} | {int(b['n'])} | {_fmt(b['mean_score'])} | {_fmt(b['observed_rate'])} |")
    lines += ["", "## Segments", "", f"k = {r['segments']['k']}, chosen by silhouette among stable solutions "
              "(mean bootstrap adjusted Rand index of at least 0.8 and worst of at least 0.5).", "",
              "| k | Silhouette | Stability (mean ARI) | Stability (min ARI) |", "| --- | --- | --- | --- |"]
    for s in r["segments"]["selection"]:
        lines.append(f"| {s['k']} | {_fmt(s['silhouette'])} | {_fmt(s['stability_ari'])} | {_fmt(s['stability_ari_min'])} |")
    lines += ["", "Segment names are not assigned. The team names segments after it inspects these profiles.", "",
              "| Segment | Test accounts | Test refinancing rate | Failed attempts 30d | Software errors 90d | Unresolved complaint | Repeat contacts unresolved |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for s in r["segments"]["test_profiles"]:
        lines.append(f"| {s['segment_id']} | {s['accounts']} | {_fmt(s.get('refinancing_rate'))} | {_fmt(s['pt_failed_attempts_30d'])} | "
                     f"{_fmt(s['pt_software_errors_90d'])} | {_fmt(s['svc_unresolved_complaint'])} | {_fmt(s['svc_repeat_contacts_unresolved'])} |")
    flagged = [p for p in r["proxy_check"]["results"] if p["flag"]]
    lines += ["", "## Proxy check", "",
              f"Model inputs compared with context the model does not use ({', '.join(r['proxy_check']['columns'])}). "
              f"Flag at |r| ≥ {r['proxy_check']['threshold']}. Flagged: {len(flagged)}.", ""]
    for p in r["proxy_check"]["results"][:10]:
        lines.append(f"- `{p['feature']}` vs `{p['context']}`: r = {p['correlation']}{' (flagged)' if p['flag'] else ''}")
    if not r["proxy_check"]["results"]:
        lines.append("No model input correlates with this context at |r| ≥ 0.1.")
    lines += ["", "## Limits", "",
              "- The data is synthetic. Outcome labels come from hidden generator factors, not real borrowers.",
              "- Attributions explain the score. They do not show the borrower's motive.",
              "- The alert threshold is a candidate frozen on validation data. The team confirms the routing threshold.",
              "- Nothing here has run in Fabric yet.", ""]
    return "\n".join(lines)


def write_report(paths: Paths, report: dict) -> dict:
    paths.reports.mkdir(parents=True, exist_ok=True)
    j = paths.reports / "evaluation_report.json"
    m = paths.reports / "evaluation_report.md"
    j.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    m.write_text(to_markdown(report), encoding="utf-8")
    return {"json": str(j), "markdown": str(m)}
