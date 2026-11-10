"""Command line: python -m models_eval <command>. Run from the notebooks/ folder.

Commands work on local Parquet folders under data/output/ (or HB_OUTPUT_ROOT).
Nothing here runs in Fabric.
"""
from __future__ import annotations

import argparse
import json

from . import config, evaluate, pipeline, policy, publish, train


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="models_eval")
    ap.add_argument("--out", default=None, help="Output root. Default: data/output or HB_OUTPUT_ROOT.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train", help="Train and register one model release.")
    t.add_argument("--no-mlflow", action="store_true")
    t.add_argument("--threshold-rule", choices=config.THRESHOLD_RULES, help="How the alert threshold is chosen. Default: min_precision (or HB_THRESHOLD_RULE).")
    t.add_argument("--min-precision", type=float, help="min_precision rule: the share of validation alerts that must be real leavers. Default 0.6 (or HB_MIN_PRECISION).")
    t.add_argument("--cost-missed-leaver", type=float, help="min_cost rule: cost of one missed leaver.")
    t.add_argument("--cost-review", type=float, help="min_cost rule: cost of one review.")
    sub.add_parser("evaluate", help="Write the later-cohort evaluation report.").add_argument("--model-version")
    s = sub.add_parser("pipeline", help="Run pl_scheduled_scoring locally: snapshots, scoring, publication.")
    s.add_argument("--cutoff", required=True)
    s.add_argument("--kind", default="scheduled", choices=config.RUN_KINDS)
    s.add_argument("--mortgage", action="append")
    s.add_argument("--model-version")
    s.add_argument("--cluster-model-version")
    s.add_argument("--started-at", help="Simulated run start. Default: one minute after HB_AS_OF when set, else now.")
    s.add_argument("--inject-failure", choices=["before_outputs", "after_outputs"], help="T05 demo.")
    sub.add_parser("publish", help="Retry pending risk_updated publication.")
    pol = sub.add_parser("policy", help="Write the routing policy from the release. The threshold comes from the release.")
    pol.add_argument("--model-version")
    a = ap.parse_args(argv)
    paths = config.paths(a.out)

    if a.cmd == "train":
        r = train.train_release(paths, use_mlflow=not a.no_mlflow, rule=config.threshold_rule(a.threshold_rule, a.cost_missed_leaver, a.cost_review, a.min_precision))
        out = {k: r[k] for k in ("release_id", "champion", "model_version", "cluster_model_version", "alert_threshold", "alert_threshold_rule")}
        out["validation_ap"] = {k: round(v["validation_ap"], 4) for k, v in r["validation"].items()}
        out["segments_k"] = r["segments"]["k"]
    elif a.cmd == "evaluate":
        rep = evaluate.evaluate_release(paths, a.model_version)
        out = {"files": evaluate.write_report(paths, rep),
               "mean_lift_at_10": {k: m["mean_lift_at_10"] for k, m in rep["models"].items()},
               "average_precision": {k: m["average_precision"] for k, m in rep["models"].items()}}
    elif a.cmd == "pipeline":
        out = pipeline.run(paths, a.cutoff, a.kind, a.mortgage, a.model_version, a.cluster_model_version,
                           a.started_at, a.inject_failure)
    elif a.cmd == "policy":
        out = policy.write(paths, a.model_version)
    else:
        out = publish.publish_pending(paths)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
