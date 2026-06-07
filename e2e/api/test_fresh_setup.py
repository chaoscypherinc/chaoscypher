# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for first-time setup flow."""

import time

import httpx
import pytest


@pytest.mark.fresh
class TestFreshSetup:
    """Test the first-time setup wizard API flow."""

    def test_setup_completed(self, client: httpx.Client, e2e_phase: str) -> None:
        """After setup, ``setup_needed`` is False on the status endpoint."""
        if e2e_phase != "fresh":
            pytest.skip("Fresh phase only")
        resp = client.get("/api/v1/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["setup_needed"] is False
        assert data["authenticated"] is True

    def test_setup_not_repeatable(self, base_url: str, e2e_phase: str) -> None:
        """Calling setup again after initial bootstrap returns 409."""
        if e2e_phase != "fresh":
            pytest.skip("Fresh phase only")
        # /auth/setup is in the auth rate-limit zone (5 r/s burst 3);
        # 20 attempts × 2 s = 40 s headroom past any transient 429.
        for _ in range(20):
            resp = httpx.post(
                f"{base_url}/api/v1/auth/setup",
                json={
                    "username": "hacker",
                    "password": "HackAttempt123",
                    "email": "hack@test.com",
                },
                timeout=10.0,
            )
            if resp.status_code != 429:
                break
            time.sleep(2.0)
        assert resp.status_code == 409

    def test_health_endpoint_accessible(self, client: httpx.Client, e2e_phase: str) -> None:
        """Health endpoint returns structured response after setup."""
        if e2e_phase != "fresh":
            pytest.skip("Fresh phase only")
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "healthy" in data
        assert "checks" in data
