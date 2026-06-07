# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for search endpoints."""

import httpx


class TestSearch:
    """Test keyword search and search stats."""

    def test_keyword_search(self, client: httpx.Client) -> None:
        """Keyword search returns results."""
        resp = client.get("/api/v1/search", params={"q": "test", "search_type": "keyword"})
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_search_no_results(self, client: httpx.Client) -> None:
        """Searching for gibberish returns empty results."""
        resp = client.get(
            "/api/v1/search",
            params={"q": "xyzzy_nonexistent_12345", "search_type": "keyword"},
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 0

    def test_search_stats(self, client: httpx.Client) -> None:
        """Search stats returns index information."""
        resp = client.get("/api/v1/search/stats")
        assert resp.status_code == 200
        assert "fulltext_doc_count" in resp.json()
