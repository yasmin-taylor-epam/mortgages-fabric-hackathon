"""Model release registry.

MLflow records each release: parameters, validation metrics, the feature
signature and the fitted estimators. It registers the champion classifier as
hb-refi-clf and the cluster pipeline as hb-segments.

A local release folder (data/output/models/releases/<release_id>/) keeps the
same fitted objects and a release.json with the frozen settings. Scoring loads
pinned versions from MLflow when it is available, and from the release folder
otherwise. Locally MLflow uses a SQLite store under data/output/mlflow/.
"""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

import joblib
import pandas as pd

from .config import CLASSIFIER_NAME, CLUSTER_NAME, EXPERIMENT_NAME, Paths

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


def _mlflow(paths: Paths):
    try:
        import mlflow
    except ImportError:
        return None
    uri = paths.mlflow_uri
    if uri:
        if uri.startswith("sqlite:///"):
            Path(uri[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        mlflow.set_tracking_uri(uri)
        mlflow.set_registry_uri(uri)
    return mlflow


def _releases_dir(paths: Paths) -> Path:
    return paths.models / "releases"


def list_releases(paths: Paths) -> list[dict]:
    d = _releases_dir(paths)
    if not d.exists():
        return []
    out = [json.loads((p / "release.json").read_text(encoding="utf-8")) for p in sorted(d.iterdir()) if (p / "release.json").exists()]
    return sorted(out, key=lambda r: r["created_at"])


def _next_local_version(paths: Paths, key: str) -> int:
    return 1 + sum(1 for r in list_releases(paths) if r.get(key))


def _log_model(model, name: str, registered_model_name: str | None = None):
    """MLflow 3 uses name=; MLflow 2 (likely on the Fabric runtime) uses artifact_path=."""
    import mlflow.sklearn

    kw = {"serialization_format": "cloudpickle"}
    if registered_model_name:
        kw["registered_model_name"] = registered_model_name
    try:
        return mlflow.sklearn.log_model(model, name=name, **kw)
    except TypeError:
        return mlflow.sklearn.log_model(model, artifact_path=name, **kw)


def _set_experiment(mlflow, paths: Paths) -> None:
    """Keep local artifacts under the output root, not in ./mlruns of the working folder."""
    if paths.mlflow_uri and mlflow.get_experiment_by_name(EXPERIMENT_NAME) is None:
        mlflow.create_experiment(EXPERIMENT_NAME, artifact_location=(paths.root / "mlflow" / "artifacts").resolve().as_uri())
    mlflow.set_experiment(EXPERIMENT_NAME)


def register_release(paths: Paths, release: dict, candidates: dict, segment_model, use_mlflow: bool = True) -> dict:
    """Store a release. release holds JSON-safe metadata. Returns it with versions filled in."""
    mlflow = _mlflow(paths) if use_mlflow else None
    champion = release["champion"]
    if mlflow is not None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _set_experiment(mlflow, paths)
            with mlflow.start_run(run_name=release["release_id"]) as run:
                mlflow.log_params({"champion": champion, "feature_version": release["feature_version"],
                                   "release_at": release["split"]["release_at"], "k": release["segments"]["k"],
                                   **{f"{m}.{k}": v for m, p in release["params"].items() for k, v in p.items()}})
                mlflow.log_metrics({f"validation.{m}.{k}": v for m, d in release["validation"].items() for k, v in d.items()
                                    if isinstance(v, (int, float))})
                mlflow.log_dict(release, "release.json")
                clf = _log_model(candidates[champion], "classifier", CLASSIFIER_NAME)
                seg = _log_model(segment_model, "segments", CLUSTER_NAME)
                for name, model in candidates.items():
                    if name != champion:
                        _log_model(model, f"challenger_{name}")
                release["mlflow_run_id"] = run.info.run_id
        clf_v, seg_v = clf.registered_model_version, seg.registered_model_version
        release["registry"] = "mlflow"
    else:
        clf_v = _next_local_version(paths, "model_version")
        seg_v = _next_local_version(paths, "cluster_model_version")
        release["registry"] = "local"
    release["model_version"] = f"{CLASSIFIER_NAME}:{clf_v}"
    release["cluster_model_version"] = f"{CLUSTER_NAME}:{seg_v}"

    d = _releases_dir(paths) / release["release_id"]
    d.mkdir(parents=True, exist_ok=True)
    joblib.dump({"candidates": candidates, "segment_model": segment_model}, d / "models.joblib")
    (d / "release.json").write_text(json.dumps(release, indent=2, default=str) + "\n", encoding="utf-8")
    return release


def load_release(paths: Paths, model_version: str | None = None, cluster_model_version: str | None = None) -> dict:
    """Load a pinned release. Without versions, load the latest release."""
    releases = list_releases(paths)
    if model_version:
        releases = [r for r in releases if r["model_version"] == model_version]
    if cluster_model_version:
        releases = [r for r in releases if r["cluster_model_version"] == cluster_model_version]
    if not releases:
        raise LookupError(f"No release for model_version={model_version} cluster_model_version={cluster_model_version}.")
    release = releases[-1]
    objs = joblib.load(_releases_dir(paths) / release["release_id"] / "models.joblib")
    classifier, segment_model, source = objs["candidates"][release["champion"]], objs["segment_model"], "release_folder"
    if release.get("registry") == "mlflow":
        mlflow = _mlflow(paths)
        if mlflow is not None:
            import mlflow.sklearn

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                classifier = mlflow.sklearn.load_model(f"models:/{CLASSIFIER_NAME}/{release['model_version'].split(':')[1]}")
                segment_model = mlflow.sklearn.load_model(f"models:/{CLUSTER_NAME}/{release['cluster_model_version'].split(':')[1]}")
            source = "mlflow"
    return {"release": release, "classifier": classifier, "segment_model": segment_model,
            "candidates": objs["candidates"], "source": source,
            "segment_map": {int(k): v for k, v in release["segments"]["segment_map"].items()}}


def release_id(created_at) -> str:
    return "REL-" + pd.Timestamp(created_at).tz_convert("UTC").strftime("%Y%m%dT%H%M%SZ")
