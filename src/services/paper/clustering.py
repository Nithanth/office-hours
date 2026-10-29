"""Paper clustering via embedding + UMAP + HDBSCAN.

Produces 2D coordinates and cluster assignments for a set of papers,
suitable for rendering as an interactive scatter plot. Designed for
small sets (10-50 papers) typical of a single search session.

UMAP over t-SNE: preserves inter-cluster spacing so the map reads
truthfully, deterministic with a fixed seed, and ~5-10x faster.
HDBSCAN over k-means: finds the natural number of clusters and marks
genuinely unrelated papers as noise (cluster -1) instead of forcing
every paper into a group.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from loguru import logger

from services.paper.embedding import embed_texts

# Palette that works in both light and dark themes (categorical, colorblind-safe)
_CLUSTER_COLORS = [
    "#4e79a7",  # blue
    "#f28e2b",  # orange
    "#e15759",  # red
    "#76b7b2",  # teal
    "#59a14f",  # green
    "#edc949",  # yellow
    "#af7aa1",  # purple
    "#ff9da7",  # pink
]

_NOISE_COLOR = "#9aa0a6"  # neutral grey for unclustered papers
_NOISE_ID = -1


@dataclass
class ClusterResult:
    """Output of the clustering pipeline."""

    papers: list[dict[str, Any]]
    clusters: list[dict[str, Any]]


def _umap_coords(embeddings: np.ndarray, n_components: int, n: int) -> np.ndarray:
    """UMAP reduction. n_neighbors must be < n; small-n inputs fall back to
    PCA because UMAP's neighbor graph degenerates below ~4 points."""
    import umap

    reducer = umap.UMAP(
        n_components=n_components,
        n_neighbors=max(2, min(15, n - 1)),
        min_dist=0.1,
        metric="cosine",
        random_state=42,
    )
    return np.asarray(reducer.fit_transform(embeddings))


def _pca_coords(embeddings: np.ndarray, n_components: int) -> np.ndarray:
    from sklearn.decomposition import PCA

    return np.asarray(
        PCA(n_components=n_components, random_state=42).fit_transform(embeddings)
    )


def _hdbscan_labels(embeddings: np.ndarray, n: int) -> np.ndarray:
    """Density clustering on a mid-dim UMAP space (the canonical recipe —
    HDBSCAN is unreliable in the 384-dim original space). Clustering on a
    reduced space also keeps groups visually consistent with the scatter.

    Returns one label per paper; -1 = noise (no confident group).
    """
    import hdbscan

    if n < 6:
        # HDBSCAN's minimum viable cluster needs ~2*min_cluster_size points
        return np.zeros(n, dtype=int)
    cluster_dims = min(8, n - 2)
    reduced = _umap_coords(embeddings, cluster_dims, n)
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=max(3, n // 10),
        min_samples=1,
        metric="euclidean",
    )
    return np.asarray(clusterer.fit_predict(reduced), dtype=int)


def _kmeans_fallback(embeddings: np.ndarray, n: int) -> np.ndarray:
    """Every point noise → k-means with silhouette k (the old path) so the
    viz always has something to show."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    if n < 4:
        return np.zeros(n, dtype=int)
    best_k, best_score, best_labels = 2, -1.0, None
    for k in range(2, min(6, n // 2) + 1):
        labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(
            embeddings
        )
        if len(set(labels)) < 2:
            continue
        score = silhouette_score(embeddings, labels)
        if score > best_score:
            best_k, best_score, best_labels = k, score, labels
    if best_labels is None or best_score < 0.15:
        return np.zeros(n, dtype=int)
    logger.debug("kmeans_fallback", best_k=best_k, silhouette=round(best_score, 3))
    return np.asarray(best_labels, dtype=int)


def _generate_cluster_labels(
    titles_by_cluster: dict[int, list[str]],
) -> dict[int, str]:
    """Generate human-readable labels for each cluster using TF-IDF on titles."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    if not titles_by_cluster:
        return {}

    # Build one "document" per cluster (concatenated titles)
    cluster_ids = sorted(titles_by_cluster.keys())
    docs = [" ".join(titles_by_cluster[cid]) for cid in cluster_ids]

    if len(docs) < 2:
        # Single cluster — just pick most common words
        return {cluster_ids[0]: "All Papers"}

    try:
        vectorizer = TfidfVectorizer(
            max_features=50,
            stop_words="english",
            ngram_range=(1, 2),
            min_df=1,
        )
        tfidf_matrix = vectorizer.fit_transform(docs)
        feature_names = vectorizer.get_feature_names_out()

        labels: dict[int, str] = {}
        # Terms already used by an earlier cluster's label — clusters whose
        # top TF-IDF terms overlap read identically ("Neural/Implicit" vs
        # "Implicit/Neural"); skip repeats so labels stay distinctive.
        used_terms: set[str] = set()
        for idx, cid in enumerate(cluster_ids):
            row = tfidf_matrix[idx].toarray().flatten()
            top_terms: list[str] = []
            for i in row.argsort()[::-1]:
                if row[i] <= 0:
                    break
                term = feature_names[i]
                if term in used_terms:
                    continue
                top_terms.append(term)
                used_terms.add(term)
                if len(top_terms) == 2:
                    break
            label = " / ".join(top_terms).title() if top_terms else f"Group {cid + 1}"
            labels[cid] = label
        return labels
    except Exception as exc:
        logger.warning("cluster_label_generation_failed", err=str(exc))
        return {cid: f"Group {cid + 1}" for cid in cluster_ids}


def cluster_papers(papers: list[dict[str, Any]]) -> ClusterResult:
    """Run the full clustering pipeline on a list of paper dicts.

    Parameters
    ----------
    papers
        List of dicts with at least ``id``, ``title``. Optionally
        ``highlights`` (str), ``authors`` (list[str]), ``year`` (str).

    Returns
    -------
    ClusterResult
        Papers with x, y, cluster fields + cluster metadata. Cluster id -1
        is the noise bucket ("Miscellaneous") for papers HDBSCAN couldn't
        place confidently.
    """
    n = len(papers)
    if n == 0:
        return ClusterResult(papers=[], clusters=[])

    # 1. Build text for embedding: title + highlights (if available)
    texts = []
    for p in papers:
        parts = [p.get("title", "")]
        if p.get("highlights"):
            parts.append(p["highlights"])
        texts.append(" ".join(parts))

    # 2. Embed
    embeddings = embed_texts(texts)  # shape: (n, 384)
    logger.debug("clustering_embeddings_computed", n=n, dim=embeddings.shape[1])

    # 3. Dimensionality reduction for display
    if n < 4:
        # Below ~4 points the neighbor graph can't form; PCA is honest there.
        coords = np.zeros((1, 2)) if n == 1 else _pca_coords(embeddings, 2)
    else:
        try:
            coords = _umap_coords(embeddings, 2, n)
        except Exception as exc:  # numba/edge-case failures shouldn't kill it
            logger.warning("umap_failed_falling_back_to_pca", err=str(exc))
            coords = _pca_coords(embeddings, 2)

    # Normalize coords to [-1, 1] range for consistent frontend rendering
    if n > 1:
        mins = coords.min(axis=0)
        maxs = coords.max(axis=0)
        ranges = maxs - mins
        ranges[ranges == 0] = 1.0  # avoid division by zero
        coords = 2 * (coords - mins) / ranges - 1

    # 4. Clustering — HDBSCAN on a mid-dim UMAP space; all-noise → k-means
    try:
        labels = _hdbscan_labels(embeddings, n)
    except Exception as exc:
        logger.warning("hdbscan_failed_falling_back_to_kmeans", err=str(exc))
        labels = _kmeans_fallback(embeddings, n)
    else:
        if n >= 6 and set(labels.tolist()) == {_NOISE_ID}:
            labels = _kmeans_fallback(embeddings, n)
    k = len(set(labels.tolist()) - {_NOISE_ID})

    # 5. Generate cluster labels (noise bucket gets a fixed name)
    titles_by_cluster: dict[int, list[str]] = {}
    for i, p in enumerate(papers):
        cid = int(labels[i])
        titles_by_cluster.setdefault(cid, []).append(p.get("title", ""))

    cluster_labels = _generate_cluster_labels(
        {cid: ts for cid, ts in titles_by_cluster.items() if cid != _NOISE_ID}
    )
    cluster_labels[_NOISE_ID] = "Miscellaneous"

    # 6. Build response
    result_papers = []
    for i, p in enumerate(papers):
        cid = int(labels[i])
        result_papers.append(
            {
                "id": p.get("id", ""),
                "x": round(float(coords[i][0]), 4),
                "y": round(float(coords[i][1]), 4),
                "cluster": cid,
                "title": p.get("title", ""),
                "authors": p.get("authors", []),
                "year": p.get("year", ""),
                "citation_count": p.get("citation_count"),
            }
        )

    result_clusters = []
    for cid in sorted(set(int(label) for label in labels)):
        color = (
            _NOISE_COLOR
            if cid == _NOISE_ID
            else _CLUSTER_COLORS[cid % len(_CLUSTER_COLORS)]
        )
        result_clusters.append(
            {
                "id": cid,
                "label": cluster_labels.get(cid, f"Group {cid + 1}"),
                "color": color,
                "count": int((labels == cid).sum()),
            }
        )

    logger.info(
        "clustering_complete",
        n_papers=n,
        n_clusters=k,
        n_noise=int((labels == _NOISE_ID).sum()),
        labels=[c["label"] for c in result_clusters],
    )
    return ClusterResult(papers=result_papers, clusters=result_clusters)
