"""Behavioural clusters (blueprint section 04, Models and segments).

Fit two to five clusters on product-transfer request and servicing features.
Fit once per model release. Each scheduled run only assigns accounts, so a
segment_id keeps its meaning until the next release. Segment names follow a
human inspection of the profiles. This module does not name them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from . import features

MIN_STABILITY = 0.8
MIN_WORST_STABILITY = 0.5


def _fit(x: pd.DataFrame, k: int, seed: int) -> Pipeline:
    return Pipeline([("scale", StandardScaler()), ("kmeans", KMeans(n_clusters=k, n_init=10, random_state=seed))]).fit(x)


def select_k(x: pd.DataFrame, ks=range(2, 6), n_boot: int = 20, seed: int = 0) -> pd.DataFrame:
    """Silhouette and bootstrap stability (mean adjusted Rand index) for each k."""
    rng = np.random.default_rng(seed)
    rows = []
    sample = x.sample(min(len(x), 2000), random_state=seed)
    for k in ks:
        base = _fit(x, k, seed)
        labels = base.predict(x)
        sil = silhouette_score(base.named_steps["scale"].transform(sample), base.predict(sample))
        aris = []
        for b in range(n_boot):
            idx = rng.integers(0, len(x), len(x))
            boot = _fit(x.iloc[idx], k, seed + b + 1)
            aris.append(adjusted_rand_score(labels, boot.predict(x)))
        rows.append({"k": k, "silhouette": round(float(sil), 4), "stability_ari": round(float(np.mean(aris)), 4),
                     "stability_ari_min": round(float(np.min(aris)), 4)})
    return pd.DataFrame(rows)


def choose_k(table: pd.DataFrame) -> int:
    """Best silhouette among stable solutions. If none is stable, the most stable k.

    Stable: mean bootstrap ARI >= 0.8 and worst bootstrap ARI >= 0.5.
    """
    stable = table[(table["stability_ari"] >= MIN_STABILITY) & (table["stability_ari_min"] >= MIN_WORST_STABILITY)]
    if len(stable):
        return int(stable.sort_values(["silhouette", "k"], ascending=[False, True]).iloc[0]["k"])
    return int(table.sort_values(["stability_ari", "k"], ascending=[False, True]).iloc[0]["k"])


def fit_segments(snap: pd.DataFrame, seed: int = 0, n_boot: int = 20) -> dict:
    x = features.cluster_matrix(snap)
    table = select_k(x, seed=seed, n_boot=n_boot)
    k = choose_k(table)
    model = _fit(x, k, seed)
    raw = model.predict(x)
    # Stable IDs: SEG-1 is the largest cluster in the training data.
    order = pd.Series(raw).value_counts().sort_index().sort_values(ascending=False, kind="stable").index.tolist()
    mapping = {int(c): f"SEG-{i + 1}" for i, c in enumerate(order)}
    return {"model": model, "k": k, "selection": table, "segment_map": mapping, "features": list(x.columns)}


def assign(model: Pipeline, segment_map: dict, snap: pd.DataFrame) -> pd.Series:
    raw = model.predict(features.cluster_matrix(snap))
    return pd.Series([segment_map[int(c)] for c in raw], index=snap.index, name="segment_id")


def profiles(snap: pd.DataFrame, segment_id: pd.Series, label: pd.Series | None = None) -> pd.DataFrame:
    """Mean of each behavioural input per segment, in original units, for inspection."""
    x = features.build_matrix(snap)[features.CLUSTER_FEATURES]
    g = x.groupby(segment_id.values)
    prof = g.mean().round(3)
    prof.insert(0, "accounts", g.size())
    if label is not None:
        prof.insert(1, "refinancing_rate", pd.Series(label.values).groupby(segment_id.values).mean().round(3))
    prof.index.name = "segment_id"
    return prof.reset_index()
