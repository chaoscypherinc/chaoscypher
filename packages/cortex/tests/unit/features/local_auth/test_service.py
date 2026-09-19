# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for LocalAuthService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.local_auth import (
    ApiKeyNotFound,
    CredentialsAlreadyInitialized,
    CredentialsFile,
    InvalidPassword,
    InvalidSessionCookie,
    UsernameMismatch,
    compute_api_key_selector,
    generate_api_key,
    hash_api_key,
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
    with pytest.raises(CredentialsAlreadyInitialized):
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
    from chaoscypher_core.services.local_auth import api_key_lookup

    # Track whether the bcrypt verify was called at all.
    calls = {"verify": 0}

    def _fake_verify(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["verify"] += 1
        return False

    monkeypatch.setattr(api_key_lookup, "verify_api_key", _fake_verify)

    # Build a service with a fake creds store that has one key
    creds = MagicMock(spec=CredentialsFile)
    creds.get_legacy_api_key_hashes.return_value = [("key-1", "$2b$12$dummy")]

    svc = LocalAuthService(
        credentials=creds,
        session_secret=b"\x00" * 32,
        cookie_ttl_seconds=3600,
    )

    assert svc.verify_api_key("not-a-real-prefix-xxx") is None
    assert calls["verify"] == 0


def test_created_key_is_stored_with_a_selector(service: LocalAuthService) -> None:
    """A freshly minted key is indexed, so it never enters the migration loop."""
    service.setup("admin", "password123")
    resp = service.create_api_key("CLI")

    creds = service._creds
    assert creds.get_legacy_api_key_hashes() == []
    secret = creds.get_or_create_api_key_selector_secret()
    found = creds.find_api_key_by_selector(compute_api_key_selector(resp.key, secret))
    assert found is not None
    assert found[0] == resp.id


def test_unknown_key_does_zero_bcrypt_work(service: LocalAuthService, monkeypatch) -> None:
    """The DoS regression: a wrong cc_live_ key costs no bcrypt verifies."""
    from chaoscypher_core.services.local_auth import api_key_lookup

    service.setup("admin", "password123")
    for name in ("one", "two", "three"):
        service.create_api_key(name)

    calls = {"verify": 0}

    def _counting(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["verify"] += 1
        return False

    monkeypatch.setattr(api_key_lookup, "verify_api_key", _counting)
    assert service.verify_api_key(generate_api_key()) is None
    assert calls["verify"] == 0


def test_legacy_key_verifies_once_then_is_indexed(service: LocalAuthService) -> None:
    """A pre-selector key keeps working and is migrated on first use."""
    service.setup("admin", "password123")
    key = generate_api_key()
    creds = service._creds
    key_id = creds.add_api_key("old", hash_api_key(key))
    assert creds.get_legacy_api_key_hashes() == [(key_id, creds.get_api_key_hashes()[0][1])]

    assert service.verify_api_key(key) == key_id
    assert creds.get_legacy_api_key_hashes() == []
