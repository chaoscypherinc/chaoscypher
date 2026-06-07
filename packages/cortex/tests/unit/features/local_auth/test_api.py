# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Integration tests for /api/v1/auth routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.services.local_auth import CredentialsFile
from chaoscypher_cortex.features.local_auth.api import build_router
from chaoscypher_cortex.features.local_auth.service import LocalAuthService


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    """FastAPI app wired with a fresh LocalAuthService per test."""
    fastapi_app = FastAPI()
    service = LocalAuthService(
        credentials=CredentialsFile(tmp_path / "creds.json"),
        session_secret=b"y" * 32,
        cookie_ttl_seconds=60,
    )
    fastapi_app.include_router(build_router(service, cookie_name="cc_session", cookie_secure=False))
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Bound TestClient for the fixture app."""
    return TestClient(app)


def test_status_before_setup(client: TestClient) -> None:
    """Before first-run setup, status reports setup_needed=True."""
    r = client.get("/api/v1/auth/status")
    assert r.status_code == 200
    assert r.json() == {
        "setup_needed": True,
        "authenticated": False,
        "username": None,
    }


def test_setup_rejects_password_under_8_chars(client: TestClient) -> None:
    """Passwords under 8 chars must be rejected (422 validation error)."""
    r = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "Short1"},  # 6 chars
    )
    assert r.status_code == 422


def test_setup_creates_session(client: TestClient) -> None:
    """POST /setup returns 201 and sets the session cookie."""
    r = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    assert r.status_code == 201
    assert "cc_session" in r.cookies


def test_setup_twice_409(client: TestClient) -> None:
    """Second /setup call must return 409 Conflict."""
    first = client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    assert first.status_code == 201
    r = client.post(
        "/api/v1/auth/setup",
        json={"username": "second", "password": "PasswordPassword2"},
    )
    assert r.status_code == 409


def test_login_happy_path(client: TestClient) -> None:
    """POST /login with correct credentials issues a session cookie."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    assert r.status_code == 200
    assert "cc_session" in r.cookies


def test_login_wrong_password_401(client: TestClient) -> None:
    """Wrong password must return 401."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "WrongPassword1234"},
    )
    assert r.status_code == 401


def test_login_wrong_username_401(client: TestClient) -> None:
    """Wrong username must return 401."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    client.cookies.clear()
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "nope", "password": "PasswordPassword1"},
    )
    assert r.status_code == 401


def test_login_before_setup_409(client: TestClient) -> None:
    """Login before setup must return 409."""
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    assert r.status_code == 409


def test_verify_200_with_valid_cookie(client: TestClient) -> None:
    """/verify returns 200 + X-Auth-User header with a valid cookie."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    r = client.get("/api/v1/auth/verify")
    assert r.status_code == 200
    assert r.headers.get("X-Auth-User") == "admin"


def test_verify_401_without_cookie(client: TestClient) -> None:
    """/verify returns 401 for unauthenticated callers."""
    r = client.get("/api/v1/auth/verify")
    assert r.status_code == 401


def test_verify_with_api_key(client: TestClient) -> None:
    """/verify accepts a valid Authorization: Bearer <api-key> header."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    r = client.post("/api/v1/auth/keys", json={"name": "CLI"})
    key = r.json()["key"]
    client.cookies.clear()
    r = client.get("/api/v1/auth/verify", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert r.headers.get("X-Auth-User") == "admin"


def test_verify_with_bad_api_key_401(client: TestClient) -> None:
    """/verify rejects an invalid bearer token."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    client.cookies.clear()
    r = client.get(
        "/api/v1/auth/verify",
        headers={"Authorization": "Bearer cc_live_definitely_wrong"},
    )
    assert r.status_code == 401


def test_logout_clears_cookie(client: TestClient) -> None:
    """POST /logout returns 204 and subsequent /verify fails."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    r = client.post("/api/v1/auth/logout")
    assert r.status_code == 204
    r = client.get("/api/v1/auth/verify")
    assert r.status_code == 401


def test_me_returns_username(client: TestClient) -> None:
    """GET /me returns the authenticated username."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json() == {"username": "admin"}


def test_me_401_without_auth(client: TestClient) -> None:
    """GET /me without auth returns 401."""
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_create_and_list_keys(client: TestClient) -> None:
    """Create two API keys and list them without exposing secret material."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    client.post("/api/v1/auth/keys", json={"name": "one"})
    client.post("/api/v1/auth/keys", json={"name": "two"})
    r = client.get("/api/v1/auth/keys")
    assert r.status_code == 200
    names = {k["name"] for k in r.json()}
    assert names == {"one", "two"}
    for item in r.json():
        assert "key" not in item
        assert "hash" not in item


def test_revoke_key(client: TestClient) -> None:
    """Revoked keys immediately stop authenticating."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    create = client.post("/api/v1/auth/keys", json={"name": "tmp"})
    key_id = create.json()["id"]
    plaintext = create.json()["key"]
    r = client.delete(f"/api/v1/auth/keys/{key_id}")
    assert r.status_code == 204
    client.cookies.clear()
    r = client.get("/api/v1/auth/verify", headers={"Authorization": f"Bearer {plaintext}"})
    assert r.status_code == 401


def test_revoke_unknown_key_404(client: TestClient) -> None:
    """Revoking an unknown key id returns 404."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    r = client.delete("/api/v1/auth/keys/k_doesnotexist")
    assert r.status_code == 404


def test_password_change_invalidates_old_session(client: TestClient) -> None:
    """Password change bumps session epoch, invalidating outstanding cookies."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "OldPassword123456"},
    )
    r = client.post(
        "/api/v1/auth/password",
        json={"old_password": "OldPassword123456", "new_password": "NewPassword123456"},
    )
    assert r.status_code == 204
    # The endpoint cleared the cookie; but also the epoch bumped, so even the
    # preserved cookie in the jar wouldn't verify. Confirm via /verify.
    r = client.get("/api/v1/auth/verify")
    assert r.status_code == 401


def test_password_change_wrong_old_403(client: TestClient) -> None:
    """Supplying the wrong old password returns 403."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "OldPassword123456"},
    )
    r = client.post(
        "/api/v1/auth/password",
        json={"old_password": "WrongPassword12345", "new_password": "NewPassword123456"},
    )
    assert r.status_code == 403


def test_username_change_issues_new_cookie(client: TestClient) -> None:
    """Renaming the admin account issues a fresh cookie bound to new name."""
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )
    r = client.post(
        "/api/v1/auth/username",
        json={"password": "PasswordPassword1", "new_username": "newname"},
    )
    assert r.status_code == 200
    assert r.json() == {"username": "newname"}
    # New cookie should have been set on the response; /me reflects the new name.
    r = client.get("/api/v1/auth/me")
    assert r.json() == {"username": "newname"}


def test_logout_invalidates_existing_session_cookie(app: FastAPI) -> None:
    """After /logout, the previously-issued cookie is no longer accepted.

    TestClient follows redirects and merges cookies automatically, so we must
    use follow_redirects=False and manually reattach the saved cookie value to
    confirm the server-side epoch bump rejects it.
    """
    client = TestClient(app, follow_redirects=False)

    # Bootstrap: setup creates a session cookie.
    client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "PasswordPassword1"},
    )

    # Capture the raw cookie value while it still works.
    old_cookie_value = client.cookies.get("cc_session")
    assert old_cookie_value is not None, "Setup should have issued a session cookie"

    # Confirm the cookie authenticates successfully before logout.
    me_before = client.get("/api/v1/auth/me")
    assert me_before.status_code == 200

    # Logout — this should bump the session epoch server-side.
    logout_resp = client.post("/api/v1/auth/logout")
    assert logout_resp.status_code == 204

    # Re-attach the OLD cookie value (the client jar was cleared by logout's
    # delete_cookie response, so we set it explicitly).
    client.cookies.set("cc_session", old_cookie_value)

    # The old cookie must now be rejected because the epoch advanced.
    me_after = client.get("/api/v1/auth/me")
    assert me_after.status_code == 401
