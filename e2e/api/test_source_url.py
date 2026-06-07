# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for source URL upload endpoint.

Note: These tests verify the API contract. Actual URL fetching may fail
in test environments without internet access - we accept either success
or appropriate failure codes.
"""

import httpx


class TestSourceUrlUpload:
    """Test POST /sources/url endpoint."""

    def test_invalid_url_format(self, client: httpx.Client) -> None:
        """Invalid URL format returns 422 (Pydantic validation)."""
        resp = client.post(
            "/api/v1/sources/url",
            json={"url": "not-a-url", "extract_entities": False},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"] == "VALIDATION_FAILED"

    def test_missing_url(self, client: httpx.Client) -> None:
        """Missing URL field returns 422."""
        resp = client.post(
            "/api/v1/sources/url",
            json={"extract_entities": False},
        )
        assert resp.status_code == 422

    def test_non_http_url_rejected(self, client: httpx.Client) -> None:
        """Non-HTTP URLs are rejected with 422 from the field validator."""
        resp = client.post(
            "/api/v1/sources/url",
            json={"url": "ftp://example.com/file", "extract_entities": False},
        )
        assert resp.status_code == 422
