# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for advanced search features (semantic, hybrid, index rebuild)."""

import httpx


class TestSearchModes:
    """Test different search modes."""

    def test_semantic_search(self, client: httpx.Client) -> None:
        """Semantic search endpoint returns results or empty list.

        The e2e stack's default embedding.provider is "local"
        (settings.py:2748-2751, sentence-transformers, no external
        service dependency), so this endpoint doesn't share the
        LLM-unconfigured skip condition the sources/extraction tests
        do. 400 only fires for an invalid search_type literal (not
        reachable here — "semantic" is valid), and 503 only comes from
        the app-wide upgrade-gate middleware (middleware.py:190-197),
        not from anything semantic-search-specific.
        """
        resp = client.get(
            "/api/v1/search",
            params={"q": "technology", "search_type": "semantic"},
        )
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_hybrid_search(self, client: httpx.Client) -> None:
        """Hybrid search endpoint returns results."""
        resp = client.get(
            "/api/v1/search",
            params={"q": "test", "search_type": "hybrid"},
        )
        assert resp.status_code == 200
        assert "data" in resp.json()


class TestSearchIndexStatus:
    """Test search index status and rebuild."""

    def test_get_index_status(self, client: httpx.Client) -> None:
        """Index status endpoint returns rebuild state."""
        resp = client.get("/api/v1/search/indexes/status")
        assert resp.status_code == 200
        data = resp.json()
        # Expected fields
        assert "needs_rebuild" in data or "embedding_model" in data
