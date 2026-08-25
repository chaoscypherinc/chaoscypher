# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for lexicon (package registry) endpoints.

Note: Many lexicon endpoints require network connectivity to the registry.
We test the endpoints that work without external dependencies.
"""

import httpx


class TestLexiconAuth:
    """Test lexicon auth status endpoint."""

    def test_auth_status(self, client: httpx.Client) -> None:
        """Lexicon auth status returns current state."""
        resp = client.get("/api/v1/lexicon/auth/status")
        # Auth status is a pure local read of stored credentials — no
        # network path, so 503 is not a reachable outcome here.
        assert resp.status_code == 200
        assert "authenticated" in resp.json()
