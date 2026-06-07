# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for LocalAuthService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.local_auth import (
    ApiKeyNotFound,
    CredentialsFile,
    InvalidPassword,
    InvalidSessionCookie,
    UsernameMismatch,
)
from chaoscypher_cortex.features.local_auth.service import LocalAuthService


@pytest.fixture
def service(tmp_path: Path) -> LocalAuthService:
    cred_path = tmp_path / "credentials.json"
    secret = b"x" * 32
    return LocalAuthService(
        credentials=CredentialsFile(cred_path),
        session_secret=secret,
        cookie_ttl_seconds=60,
    )


def test_status_before_setup(service: LocalAuthService) -> None:
    status = service.status(session_cookie=None)
    assert status.setup_needed is True
    assert status.authenticated is False
    assert status.username is None


def test_status_after_setup_without_cookie(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    status = service.status(session_cookie=None)
    assert status.setup_needed is False
    assert status.authenticated is False


def test_status_with_valid_cookie(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    cookie = service.login("admin", "password123")
    status = service.status(session_cookie=cookie)
    assert status.setup_needed is False
    assert status.authenticated is True
    assert status.username == "admin"


def test_setup_twice_fails(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    with pytest.raises(FileExistsError):
        service.setup("other", "password456")


def test_login_wrong_password(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    with pytest.raises(InvalidPassword):
        service.login("admin", "wrong")


def test_login_wrong_username(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    with pytest.raises(UsernameMismatch):
        service.login("other", "password123")


def test_verify_accepts_valid_cookie(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    cookie = service.login("admin", "password123")
    assert service.verify_session_cookie(cookie) == "admin"


def test_verify_rejects_cookie_after_password_change(service: LocalAuthService) -> None:
    """Password change bumps session_epoch → old cookies invalid."""
    service.setup("admin", "password123")
    cookie = service.login("admin", "password123")
    service.change_password("admin", "password123", "new-password")
    with pytest.raises(InvalidSessionCookie):
        service.verify_session_cookie(cookie)


def test_verify_api_key_match(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    resp = service.create_api_key("CLI")
    assert service.verify_api_key(resp.key) == resp.id


def test_verify_api_key_unknown(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    assert service.verify_api_key("cc_live_unknown") is None


def test_revoke_api_key_invalidates_it(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    resp = service.create_api_key("CLI")
    service.revoke_api_key(resp.id)
    assert service.verify_api_key(resp.key) is None


def test_revoke_nonexistent_raises(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    with pytest.raises(ApiKeyNotFound):
        service.revoke_api_key("k_nonexistent")


def test_list_api_keys_excludes_hashes(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    service.create_api_key("one")
    service.create_api_key("two")
    items = service.list_api_keys()
    assert {i.name for i in items} == {"one", "two"}


def test_change_username_returns_cookie_for_new_name(service: LocalAuthService) -> None:
    service.setup("admin", "password123")
    new_cookie = service.change_username("admin", "password123", "newname")
    assert service.verify_session_cookie(new_cookie) == "newname"


def test_verify_api_key_fast_rejects_wrong_prefix(monkeypatch) -> None:
    """Tokens without the cc_live_ prefix must not enter the bcrypt loop."""
    from chaoscypher_cortex.features.local_auth import service as svc_module

    # Track whether verify_api_key (the bcrypt one) was called
    calls = {"verify": 0}

    def _fake_verify(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["verify"] += 1
        return False

    monkeypatch.setattr(svc_module, "verify_api_key", _fake_verify)

    # Build a service with a fake creds store that has one key
    creds = MagicMock(spec=CredentialsFile)
    creds.get_api_key_hashes.return_value = [("key-1", "$2b$12$dummy")]

    svc = LocalAuthService(
        credentials=creds,
        session_secret=b"\x00" * 32,
        cookie_ttl_seconds=3600,
    )

    assert svc.verify_api_key("not-a-real-prefix-xxx") is None
    assert calls["verify"] == 0
