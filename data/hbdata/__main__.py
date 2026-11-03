"""Command line: python -m hbdata <command>. Run from the data/ folder.

Commands run locally on Parquet folders under data/output/. Nothing here runs in Fabric.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from . import fixtures, generator, io, pipelines_local, replay, snapshots, validation

OUT = io.OUTPUT_DIR


def _p(args):
    root = Path(args.out)
    return root / "exports", root / "lakehouse", root / "eventhouse"


def cmd_generate(args):
    cfg = generator.GenConfig(seed=args.seed, per_cohort=args.per_cohort, open_cohort_size=args.open_cohort_size)
    frames, manifest = generator.generate(cfg)
    exports, _, _ = _p(args)
    manifest = generator.write_exports(frames, manifest, exports)
    print(json.dumps({"exports": str(exports), "row_counts": manifest["row_counts"],
                      "label_rate_by_cohort": manifest["label_rate_by_cohort"]}, indent=2))


def _as_of(args):
    exports, _, _ = _p(args)
    return pd.Timestamp(json.loads((exports / "manifest.json").read_text(encoding="utf-8"))["config"]["as_of"])


def cmd_load_raw(args):
    exports, lake, _ = _p(args)
    print(json.dumps(pipelines_local.load_raw(exports, lake, args.at or _as_of(args)), indent=2))


def cmd_validate(args):
    _, lake, _ = _p(args)
    result = validation.validate(pipelines_local.read_raw(lake), args.at or _as_of(args))
    validation.write_outputs(result, lake)
    print(json.dumps({"summary": result["summary"], "quarantine_by_reason": result["quarantine_by_reason"]}, indent=2))


def cmd_snapshots(args):
    _, lake, _ = _p(args)
    clean = pipelines_local.read_clean(lake)
    as_of = pd.Timestamp(args.at) if args.at else _as_of(args)
    if args.live:
        req = snapshots.live_requests(clean["mortgage_account"], as_of, args.mortgage)
        extra = pipelines_local.read_staged_events(lake)
    else:
        req, extra = snapshots.checkpoint_requests(clean["mortgage_account"], as_of), None
    snap = snapshots.build_snapshots(clean, req, as_of, extra)
    stats = snapshots.append_snapshots(snap, lake / "features" / "feature_snapshot.parquet")
    io.write_table(clean["observed_outcome"], lake / "features" / "observed_outcome.parquet", "observed_outcome")
    print(json.dumps({"built": int(len(snap)), **stats}, indent=2))


def cmd_copy_context(args):
    _, lake, eh = _p(args)
    print(json.dumps(pipelines_local.copy_context(lake, eh, args.at or _as_of(args)), indent=2, default=str))


def cmd_replay(args):
    exports, lake, eh = _p(args)
    ctx = pd.read_parquet(eh / "account_context.parquet")
    if args.backfill:
        clean = pipelines_local.read_clean(lake)
        envs = replay.transfer_envelopes(clean["transfer_request_event"])
    else:
        envs = replay.read_jsonl(args.fixture)
    if args.target == "eventstream":
        print(json.dumps({"sent": replay.send_eventstream(envs)}))
        return
    print(json.dumps(replay.ingest_local(envs, eh, ctx, args.at or pd.Timestamp.now(tz="UTC")), indent=2))


def cmd_export_journeys(args):
    _, lake, eh = _p(args)
    cutoff = pd.Timestamp(args.at) if args.at else pd.Timestamp.now(tz="UTC")
    print(json.dumps(pipelines_local.export_journeys(eh, lake, cutoff, pd.Timestamp.now(tz="UTC")), indent=2))


def cmd_fixtures(args):
    print(json.dumps(fixtures.write_fixtures(), indent=2))


def cmd_all(args):
    """Generate, load, validate, build checkpoint snapshots, copy context and backfill the local Eventhouse."""
    cmd_generate(args)
    args.at = None
    cmd_load_raw(args)
    cmd_validate(args)
    args.live, args.mortgage = False, None
    cmd_snapshots(args)
    cmd_copy_context(args)
    args.backfill, args.fixture, args.target, args.at = True, None, "local", None
    cmd_replay(args)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m hbdata", description=__doc__)
    ap.add_argument("--out", default=str(OUT), help="Output root. Default: data/output.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="Write synthetic exports.")
    for p in (g, sub.add_parser("all", help="Run the full local chain.")):
        p.add_argument("--seed", type=int, default=generator.GenConfig.seed)
        p.add_argument("--per-cohort", type=int, default=generator.GenConfig.per_cohort)
        p.add_argument("--open-cohort-size", type=int, default=generator.GenConfig.open_cohort_size)
    for name in ("load-raw", "validate", "copy-context"):
        sub.add_parser(name).add_argument("--at", help="Run time (ISO 8601). Default: generator as_of.")
    s = sub.add_parser("snapshots", help="Build frozen feature snapshots.")
    s.add_argument("--at", help="Cutoff for live snapshots, or as_of for checkpoint snapshots.")
    s.add_argument("--live", action="store_true", help="Build live snapshots at --at, using staged journey events.")
    s.add_argument("--mortgage", action="append", help="Limit live snapshots to these mortgage_ids.")
    r = sub.add_parser("replay", help="Replay live events into the local Eventhouse or Eventstream.")
    r.add_argument("--fixture", type=Path, help="JSON lines file of live_event envelopes.")
    r.add_argument("--backfill", action="store_true", help="Load clean request history as live events.")
    r.add_argument("--target", choices=["local", "eventstream"], default="local")
    r.add_argument("--at", help="Ingestion time for events without simulated_ingested_at.")
    e = sub.add_parser("export-journeys", help="Stage journey results at a cutoff.")
    e.add_argument("--at", help="Cutoff (ISO 8601). Default: now.")
    sub.add_parser("fixtures", help="Rebuild data/fixtures from the generator.")
    args = ap.parse_args(argv)
    {"generate": cmd_generate, "load-raw": cmd_load_raw, "validate": cmd_validate, "snapshots": cmd_snapshots,
     "copy-context": cmd_copy_context, "replay": cmd_replay, "export-journeys": cmd_export_journeys,
     "fixtures": cmd_fixtures, "all": cmd_all}[args.cmd](args)


if __name__ == "__main__":
    main()
