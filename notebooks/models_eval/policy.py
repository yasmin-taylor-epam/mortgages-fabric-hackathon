"""Write the routing policy from a release. Nobody types the threshold in.

The routing policy holds the risk threshold that the router uses. This command copies it from the release,
together with the name of the rule that chose it. Run it after every training run.
"""
from __future__ import annotations

import json

from . import registry
from .config import MAX_SCORE_AGE_MINUTES, Paths

NOTE = ("Written by `python -m models_eval policy` from the model release. The threshold comes from the release, "
        "chosen by the named rule. max_score_age_minutes is a placeholder that the team has not confirmed.")


def build(paths: Paths, model_version: str | None = None, max_score_age_minutes: int | None = None) -> dict:
    rel = registry.load_release(paths, model_version)["release"]
    return {
        "_note": NOTE,
        "version": f"routing-policy-{rel['release_id']}",
        "release_id": rel["release_id"],
        "model_version": rel["model_version"],
        "risk_threshold": float(rel["alert_threshold"]),
        "threshold_rule": rel.get("alert_threshold_rule", "max_f1"),
        "threshold_rule_params": rel.get("alert_threshold_params", {}),
        "max_score_age_minutes": int(max_score_age_minutes or MAX_SCORE_AGE_MINUTES),
    }


def write(paths: Paths, model_version: str | None = None, max_score_age_minutes: int | None = None) -> dict:
    policy = build(paths, model_version, max_score_age_minutes)
    out = paths.root / "policy" / "routing-policy.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    return {"file": str(out), **{k: policy[k] for k in ("version", "risk_threshold", "threshold_rule", "max_score_age_minutes")}}
