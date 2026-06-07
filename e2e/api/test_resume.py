# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for warm-start (resume) phase.

These tests verify that data persists across container restarts.
They only run during the resume phase (E2E_PHASE=resume).
"""

import time

import httpx
import pytest


ADMIN_USERNAME = "e2e_admin"


def _get_with_retry(client: httpx.Client, url: str, max_attempts: int = 5) -> httpx.Response:
    """GET with retry for transient 503 (nginx rate limit on auth zone)."""
    for attempt in range(max_attempts):
        resp = client.get(url)
        if resp.status_code != 503:
            return resp
        if attempt < max_attempts - 1:
            time.sleep(1)
    return resp


@pytest.mark.resume
class TestResume:
    """Verify data persists after container restart."""

    def test_login_works(self, client: httpx.Client, e2e_phase: str) -> None:
        """Can log in with credentials from fresh phase."""
        if e2e_phase != "resume":
            pytest.skip("Resume phase only")
        resp = _get_with_retry(client, "/api/v1/auth/me")
        assert resp.status_code == 200
        assert resp.json()["username"] == ADMIN_USERNAME

    def test_setup_not_needed(self, client: httpx.Client, e2e_phase: str) -> None:
        """Setup wizard should not be needed after restart."""
        if e2e_phase != "resume":
            pytest.skip("Resume phase only")
        # Endpoint moved /auth/setup/status → /auth/status and the field
        # was renamed needs_setup → setup_needed in the same migration.
        resp = client.get("/api/v1/auth/status")
        assert resp.status_code == 200
        assert resp.json()["setup_needed"] is False

    def test_nodes_persist(self, client: httpx.Client, e2e_phase: str) -> None:
        """Nodes created in fresh phase still exist."""
        if e2e_phase != "resume":
            pytest.skip("Resume phase only")
        resp = client.get("/api/v1/nodes")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) > 0

    def test_templates_persist(self, client: httpx.Client, e2e_phase: str) -> None:
        """Templates created in fresh phase still exist."""
        if e2e_phase != "resume":
            pytest.skip("Resume phase only")
        resp = client.get("/api/v1/templates")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) > 0

    def test_search_still_works(self, client: httpx.Client, e2e_phase: str) -> None:
        """Search still functions after restart."""
        if e2e_phase != "resume":
            pytest.skip("Resume phase only")
        resp = client.get("/api/v1/search", params={"q": "test", "search_type": "keyword"})
        assert resp.status_code == 200

    def test_export_existing_data(self, client: httpx.Client, e2e_phase: str) -> None:
        """Can export the persisted graph."""
        if e2e_phase != "resume":
            pytest.skip("Resume phase only")
        resp = client.post(
            "/api/v1/exports",
            params={"include_templates": "true", "include_knowledge": "true"},
        )
        assert resp.status_code == 202
        assert "task_id" in resp.json()
