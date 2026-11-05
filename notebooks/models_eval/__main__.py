"""Command line: python -m models_eval <command>. Run from the notebooks/ folder.

Commands work on local Parquet folders under data/output/ (or HB_OUTPUT_ROOT).
Nothing here runs in Fabric.
"""
from __future__ import annotations

import argparse
import json

from . import config, evaluate, pipeline, publish, train


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="models_eval")
    ap.add_argument("--out", default=None, help="Output root. Default: data/output or HB_OUTPUT_ROOT.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train", help="Train and register one model release.")
    t.add_argument("--no-mlflow", action="store_true")
    sub.add_parser("evaluate", help="Write the later-cohort evaluation report.").add_argument("--model-version")
    s = sub.add_parser("pipeline", help="Run pl_scheduled_scoring locally: snapshots, scoring, publication.")
    s.add_argument("--cutoff", required=True)
    s.add_argument("--kind", default="scheduled", choices=config.RUN_KINDS)
    s.add_argument("--mortgage", action="append")
    s.add_argument("--model-version")
    s.add_argument("--cluster-model-version")
    s.add_argument("--started-at", help="Simulated run start. Default: now.")
    s.add_argument("--inject-failure", choices=["before_outputs", "after_outputs"], help="T05 demo.")
    sub.add_parser("publish", help="Retry pending risk_updated publication.")
    a = ap.parse_args(argv)
    paths = config.paths(a.out)

    if a.cmd == "train":
        r = train.train_release(paths, use_mlflow=not a.no_mlflow)
        out = {k: r[k] for k in ("release_id", "champion", "model_version", "cluster_model_version", "alert_threshold",
                                 "review_capacity_threshold")}
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
    else:
        out = publish.publish_pending(paths)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
