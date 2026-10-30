"""Unit tests for paper clustering service."""

import numpy as np

from services.paper.clustering import ClusterResult, cluster_papers


def _make_papers(n: int, topic_prefix: str = "paper") -> list[dict]:
    """Generate n mock paper dicts with distinct titles."""
    return [
        {
            "id": f"{topic_prefix}_{i}",
            "title": f"{topic_prefix} research on topic {i}",
            "authors": [f"Author {i}"],
            "year": str(2020 + i % 5),
            "highlights": f"This paper studies {topic_prefix} methods for problem {i}.",
        }
        for i in range(n)
    ]


class TestClusterPapers:
    """Test the clustering pipeline."""

    def test_empty_input(self):
        result = cluster_papers([])
        assert result.papers == []
        assert result.clusters == []

    def test_single_paper(self):
        papers = _make_papers(1)
        result = cluster_papers(papers)
        assert len(result.papers) == 1
        assert result.papers[0]["x"] == 0.0
        assert result.papers[0]["y"] == 0.0
        assert result.papers[0]["cluster"] == 0

    def test_two_papers(self):
        papers = _make_papers(2)
        result = cluster_papers(papers)
        assert len(result.papers) == 2
        # Two papers should have different coordinates
        assert not (
            result.papers[0]["x"] == result.papers[1]["x"]
            and result.papers[0]["y"] == result.papers[1]["y"]
        )

    def test_multiple_papers_with_clusters(self, monkeypatch):
        # Create two distinct topic groups
        group_a = [
            {
                "id": f"a{i}",
                "title": f"deep learning neural network architecture {i}",
                "authors": ["Smith"],
                "year": "2023",
                "highlights": f"transformer attention mechanism {i}",
            }
            for i in range(5)
        ]
        group_b = [
            {
                "id": f"b{i}",
                "title": f"quantum computing algorithm optimization {i}",
                "authors": ["Jones"],
                "year": "2024",
                "highlights": f"qubit entanglement measurement {i}",
            }
            for i in range(5)
        ]
        papers = group_a + group_b
        # Real embeddings are hardware-dependent — torch matmul varies enough
        # across CPU archs for UMAP/HDBSCAN to merge the groups (CI flake).
        # Two orthogonal embedding blocks keep the pipeline deterministic
        # while still exercising reduce → cluster → label end to end.
        vecs = np.zeros((10, 8))
        vecs[:5, 0] = 1.0
        vecs[5:, 4] = 1.0
        monkeypatch.setattr("services.paper.clustering.embed_texts", lambda texts: vecs)
        result = cluster_papers(papers)

        assert len(result.papers) == 10
        assert len(result.clusters) >= 2
        assert isinstance(result, ClusterResult)

        # Each paper should have x, y, cluster fields
        for p in result.papers:
            assert "x" in p
            assert "y" in p
            assert "cluster" in p
            assert "title" in p
            assert -1.0 <= p["x"] <= 1.0
            assert -1.0 <= p["y"] <= 1.0

    def test_cluster_labels_generated(self):
        papers = _make_papers(8, "attention") + _make_papers(4, "reinforcement")
        result = cluster_papers(papers)
        # Should have cluster labels
        for cluster in result.clusters:
            assert "label" in cluster
            assert "color" in cluster
            assert "id" in cluster
            assert cluster["color"].startswith("#")

    def test_papers_without_highlights(self):
        """Clustering works with title-only (no highlights)."""
        papers = [
            {
                "id": f"p{i}",
                "title": f"paper about topic {i}",
                "authors": [],
                "year": "",
            }
            for i in range(6)
        ]
        result = cluster_papers(papers)
        assert len(result.papers) == 6
        # Should not crash — falls back to title-only embedding

    def test_result_structure(self):
        papers = _make_papers(5)
        result = cluster_papers(papers)
        # Verify response matches expected JSON shape
        assert hasattr(result, "papers")
        assert hasattr(result, "clusters")
        assert all("id" in p for p in result.papers)
        assert all("x" in p for p in result.papers)
        assert all("y" in p for p in result.papers)
        assert all("cluster" in p for p in result.papers)
