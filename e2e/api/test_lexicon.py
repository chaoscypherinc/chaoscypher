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
        # May return 200 or 503 depending on config
        assert resp.status_code in (200, 503)
