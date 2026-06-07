# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for authentication endpoints."""

import time

import httpx


# Import via conftest's module-level constants (same package)
ADMIN_USERNAME = "e2e_admin"
ADMIN_PASSWORD = "E2eTestPass123"


def _get_with_retry(client: httpx.Client, url: str, max_attempts: int = 5) -> httpx.Response:
    """GET with retry for transient 503 (nginx rate limit on auth zone)."""
    for attempt in range(max_attempts):
        resp = client.get(url)
        if resp.status_code != 503:
            return resp
        if attempt < max_attempts - 1:
            time.sleep(1)
    return resp


def _post_with_retry(
    base_url: str,
    path: str,
    json: dict,
    max_attempts: int = 20,
) -> httpx.Response:
    """Bare-client POST that retries past nginx's auth-zone 429.

    The ``auth`` rate-limit zone is 5 r/s burst 3, intentional security
    control. A busy test session easily bursts through that on
    /auth/login + /auth/setup. ``20 attempts × 2 s = 40 s`` headroom
    is overkill at 5 r/s but cheap when the typical path doesn't 429.
    """
    for attempt in range(max_attempts):
        resp = httpx.post(f"{base_url}{path}", json=json, timeout=10.0)
        if resp.status_code != 429:
            return resp
        if attempt < max_attempts - 1:
            time.sleep(2.0)
    return resp


class TestAuth:
    """Test login, token refresh, and API key management."""

    def test_login_valid_credentials(self, base_url: str) -> None:
        """Login with valid credentials sets the cc_session cookie."""
        resp = _post_with_retry(
            base_url,
            "/api/v1/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        assert resp.status_code == 200
        assert resp.cookies.get("cc_session") is not None
        assert resp.json()["username"] == ADMIN_USERNAME

    def test_login_bad_password(self, base_url: str) -> None:
        """Login with wrong password returns 401."""
        resp = _post_with_retry(
            base_url,
            "/api/v1/auth/login",
            json={"username": ADMIN_USERNAME, "password": "WrongPassword99"},
        )
        assert resp.status_code == 401

    def test_protected_endpoint_no_token(self, base_url: str) -> None:
        """Accessing a protected (admin) endpoint without token returns 401."""
        resp = httpx.get(f"{base_url}/api/v1/auth/users", timeout=10.0)
        assert resp.status_code == 401

    def test_get_current_user(self, client: httpx.Client) -> None:
        """GET /auth/me returns the authenticated user profile."""
        resp = _get_with_retry(client, "/api/v1/auth/me")
        assert resp.status_code == 200
        assert resp.json()["username"] == ADMIN_USERNAME

    def test_create_and_list_api_key(self, client: httpx.Client) -> None:
        """Creating an API key returns it, listing shows it."""
        create_resp = client.post("/api/v1/auth/keys", json={"name": "e2e-test-key"})
        assert create_resp.status_code in (200, 201)
        key_data = create_resp.json()
        assert "raw_key" in key_data or "key" in key_data

        list_resp = client.get("/api/v1/auth/keys")
        assert list_resp.status_code == 200
        list_data = list_resp.json()
        # Response may be a list or wrapped in data/keys key
        keys_list = (
            list_data
            if isinstance(list_data, list)
            else list_data.get("data", list_data.get("keys", []))
        )
        key_names = [k["name"] for k in keys_list]
        assert "e2e-test-key" in key_names
