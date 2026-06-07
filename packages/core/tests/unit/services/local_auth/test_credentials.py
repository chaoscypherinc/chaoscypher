# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for CredentialsFile."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from chaoscypher_core.services.local_auth.credentials import CredentialsFile
from chaoscypher_core.services.local_auth.errors import (
    CorruptCredentialsFile,
    CredentialsNotInitialized,
    InvalidPassword,
    UsernameMismatch,
)


@pytest.fixture
def cred_path(tmp_path: Path) -> Path:
    return tmp_path / "credentials.json"


def test_is_initialized_false_when_missing(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    assert creds.is_initialized() is False


def test_initialize_creates_file_with_user(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "correct horse battery staple")
    assert creds.is_initialized() is True
    assert cred_path.exists()
    data = json.loads(cred_path.read_text())
    assert data["user"]["username"] == "admin"
    assert data["user"]["password_hash"].startswith("$2b$")
    assert data["api_keys"] == []
    assert data["session_epoch"] == 1


def test_initialize_twice_raises(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "pw")
    with pytest.raises(FileExistsError):
        creds.initialize("other", "pw2")


def test_file_has_0600_permissions(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "pw")
    if os.name == "posix":
        mode = cred_path.stat().st_mode & 0o777
        assert mode == 0o600


def test_verify_password_correct(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "correct horse")
    assert creds.verify_password("admin", "correct horse") is True


def test_verify_password_wrong(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "correct horse")
    assert creds.verify_password("admin", "wrong") is False


def test_verify_password_wrong_username(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "correct horse")
    assert creds.verify_password("not-admin", "correct horse") is False


def test_verify_password_before_init_raises(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    with pytest.raises(CredentialsNotInitialized):
        creds.verify_password("admin", "pw")


def test_change_password_succeeds(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "old-pw")
    creds.change_password("admin", "old-pw", "new-pw")
    assert creds.verify_password("admin", "new-pw") is True
    assert creds.verify_password("admin", "old-pw") is False


def test_change_password_wrong_old_raises(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "old-pw")
    with pytest.raises(InvalidPassword):
        creds.change_password("admin", "wrong", "new-pw")


def test_change_password_wrong_username_raises(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "old-pw")
    with pytest.raises(UsernameMismatch):
        creds.change_password("other", "old-pw", "new-pw")


def test_change_password_bumps_session_epoch(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "pw")
    assert creds.get_session_epoch() == 1
    creds.change_password("admin", "pw", "new-pw")
    assert creds.get_session_epoch() == 2


def test_change_username_succeeds(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "pw")
    creds.change_username("admin", "pw", "newname")
    assert creds.get_username() == "newname"
    assert creds.verify_password("newname", "pw") is True


def test_change_username_bumps_session_epoch(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "pw")
    creds.change_username("admin", "pw", "newname")
    assert creds.get_session_epoch() == 2


def test_atomic_write_no_partial_file(cred_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If write fails mid-way, the existing file must be unchanged."""
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "pw")
    original = cred_path.read_text()

    def broken_replace(src: object, dst: object) -> None:
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk full"):
        creds.change_password("admin", "pw", "new-pw")
    assert cred_path.read_text() == original


def test_load_corrupt_file_raises(cred_path: Path) -> None:
    cred_path.write_text("{not-valid-json")
    creds = CredentialsFile(cred_path)
    with pytest.raises(CorruptCredentialsFile):
        creds.verify_password("admin", "pw")


def test_touch_api_key_updates_last_used(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "pw")
    key_id = creds.add_api_key("CLI", "$2b$12$dummy_hash_value_for_test_only")
    assert creds.list_api_keys()[0]["last_used_at"] is None
    creds.touch_api_key(key_id)
    assert creds.list_api_keys()[0]["last_used_at"] is not None


def test_touch_api_key_unknown_is_silent_noop(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "pw")
    # Should not raise
    creds.touch_api_key("k_nonexistent")
