"""Paper clustering via embedding + t-SNE + k-means.

Produces 2D coordinates and cluster assignments for a set of papers,
suitable for rendering as an interactive scatter plot. Designed for
small sets (10-50 papers) typical of a single search session.
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


@dataclass
class ClusterResult:
    """Output of the clustering pipeline."""

    papers: list[dict[str, Any]]
    clusters: list[dict[str, Any]]


def _auto_select_k(embeddings: np.ndarray, max_k: int = 6) -> int:
    """Choose optimal k for k-means via silhouette score.

    Returns k=2 if no clear structure exists.
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    n = len(embeddings)
    if n < 4:
        return 1

    max_k = min(max_k, n // 2)
    if max_k < 2:
        return 1

    best_k = 2
    best_score = -1.0

    for k in range(2, max_k + 1):
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(embeddings)
        # Silhouette needs at least 2 distinct labels
        if len(set(labels)) < 2:
            continue
        score = silhouette_score(embeddings, labels)
        if score > best_score:
            best_score = score
            best_k = k

    # If silhouette is very low, the data has weak cluster structure
    if best_score < 0.15:
        best_k = 2

    logger.debug("auto_k_selection", best_k=best_k, silhouette=round(best_score, 3))
    return best_k


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
        for idx, cid in enumerate(cluster_ids):
            row = tfidf_matrix[idx].toarray().flatten()
            # Top 2-3 terms
            top_indices = row.argsort()[-3:][::-1]
            top_terms = [feature_names[i] for i in top_indices if row[i] > 0]
            label = " / ".join(top_terms[:2]).title() if top_terms else f"Group {cid + 1}"
            labels[cid] = label
        return labels
    except Exception as exc:
        logger.warning("cluster_label_generation_failed", err=str(exc))
        return {cid: f"Group {cid + 1}" for cid in cluster_ids}


def cluster_papers(
    papers: list[dict[str, Any]],
    *,
    perplexity: float | None = None,
) -> ClusterResult:
    """Run the full clustering pipeline on a list of paper dicts.

    Parameters
    ----------
    papers
        List of dicts with at least ``id``, ``title``. Optionally
        ``highlights`` (str), ``authors`` (list[str]), ``year`` (str).
    perplexity
        t-SNE perplexity. If None, auto-set to min(n-1, 10).

    Returns
    -------
    ClusterResult
        Papers with x, y, cluster fields + cluster metadata.
    """
    from sklearn.cluster import KMeans
    from sklearn.manifold import TSNE

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

    # 3. Dimensionality reduction
    if n < 3:
        # t-SNE needs at least 3 samples; for 1-2, just place manually
        coords = np.array([[0.0, 0.0]] * n)
        if n == 2:
            coords[1] = [1.0, 0.0]
    else:
        perp = perplexity or min(n - 1, 10)
        tsne_kwargs = {
            "n_components": 2,
            "perplexity": perp,
            "random_state": 42,
            "init": "pca",
            "learning_rate": "auto",
        }
        # sklearn 1.7+ renamed n_iter → max_iter
        import inspect
        tsne_params = inspect.signature(TSNE.__init__).parameters
        if "max_iter" in tsne_params:
            tsne_kwargs["max_iter"] = 1000
        else:
            tsne_kwargs["n_iter"] = 1000
        tsne = TSNE(**tsne_kwargs)
        coords = tsne.fit_transform(embeddings)

    # Normalize coords to [-1, 1] range for consistent frontend rendering
    if n > 1:
        mins = coords.min(axis=0)
        maxs = coords.max(axis=0)
        ranges = maxs - mins
        ranges[ranges == 0] = 1.0  # avoid division by zero
        coords = 2 * (coords - mins) / ranges - 1

    # 4. Clustering
    if n < 4:
        labels = np.zeros(n, dtype=int)
        k = 1
    else:
        k = _auto_select_k(embeddings)
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(embeddings)

    # 5. Generate cluster labels
    titles_by_cluster: dict[int, list[str]] = {}
    for i, p in enumerate(papers):
        cid = int(labels[i])
        titles_by_cluster.setdefault(cid, []).append(p.get("title", ""))

    cluster_labels = _generate_cluster_labels(titles_by_cluster)

    # 6. Build response
    result_papers = []
    for i, p in enumerate(papers):
        cid = int(labels[i])
        result_papers.append({
            "id": p.get("id", ""),
            "x": round(float(coords[i][0]), 4),
            "y": round(float(coords[i][1]), 4),
            "cluster": cid,
            "title": p.get("title", ""),
            "authors": p.get("authors", []),
            "year": p.get("year", ""),
        })

    result_clusters = []
    for cid in sorted(set(int(l) for l in labels)):
        result_clusters.append({
            "id": cid,
            "label": cluster_labels.get(cid, f"Group {cid + 1}"),
            "color": _CLUSTER_COLORS[cid % len(_CLUSTER_COLORS)],
            "count": int((labels == cid).sum()),
        })

    logger.info(
        "clustering_complete",
        n_papers=n,
        n_clusters=k,
        labels=[c["label"] for c in result_clusters],
    )
    return ClusterResult(papers=result_papers, clusters=result_clusters)
