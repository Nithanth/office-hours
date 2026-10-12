"""Integration tests for the /api/cluster endpoint."""

from __future__ import annotations

from starlette.testclient import TestClient


def test_cluster_endpoint_returns_valid_json(client: TestClient) -> None:
    """Cluster endpoint returns valid JSON with cluster data or error."""
    resp = client.post("/api/cluster")
    assert resp.status_code == 200
    data = resp.json()
    assert "ok" in data

    if data["ok"]:
        # Valid cluster response
        assert "papers" in data
        assert "clusters" in data
        assert isinstance(data["papers"], list)
        assert isinstance(data["clusters"], list)
        # Each paper should have coordinates and cluster assignment
        for paper in data["papers"]:
            assert "x" in paper
            assert "y" in paper
            assert "cluster" in paper
            assert "title" in paper
            assert -1.0 <= paper["x"] <= 1.0
            assert -1.0 <= paper["y"] <= 1.0
        # Each cluster should have label and color
        for cluster in data["clusters"]:
            assert "id" in cluster
            assert "label" in cluster
            assert "color" in cluster
            assert cluster["color"].startswith("#")
    else:
        # Valid error response
        assert "error" in data
