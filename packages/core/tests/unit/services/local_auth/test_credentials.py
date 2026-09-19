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
    CredentialsAlreadyInitialized,
    CredentialsNotInitialized,
    InvalidPassword,
    LocalAuthError,
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


def test_initialize_twice_raises_typed_auth_error(cred_path: Path) -> None:
    """Double initialize raises the local-auth hierarchy, not stdlib FileExistsError."""
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "pw")
    with pytest.raises(CredentialsAlreadyInitialized):
        creds.initialize("other", "pw2")


def test_credentials_already_initialized_is_local_auth_error() -> None:
    """The double-init error participates in the shared auth hierarchy."""
    assert issubclass(CredentialsAlreadyInitialized, LocalAuthError)


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


def test_not_initialized_error_never_reveals_the_path(cred_path: Path) -> None:
    """The message carries no path — it is rendered into a 401.

    Every ``LocalAuthError`` maps to HTTP 401 and its message goes into the
    response envelope, and a pre-setup install answers from this arm for every
    caller. The path an operator needs is on the exception as ``.path`` and in
    the server-side log, not on the wire.
    """
    creds = CredentialsFile(cred_path)

    with pytest.raises(CredentialsNotInitialized) as exc:
        creds.verify_password("admin", "pw")

    message = str(exc.value)
    assert str(cred_path) not in message
    assert cred_path.name not in message
    assert exc.value.path == str(cred_path)


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


def _write_corrupt_hash(cred_path: Path) -> None:
    """Persist a credentials file whose stored bcrypt hash is malformed."""
    data = json.loads(cred_path.read_text())
    data["user"]["password_hash"] = "not-a-bcrypt-hash"
    cred_path.write_text(json.dumps(data))


def test_change_password_corrupt_hash_raises_typed_error(cred_path: Path) -> None:
    """A malformed stored hash surfaces as a typed auth error, not ValueError.

    bcrypt.verify raises ValueError on a hash it cannot parse;
    ``verify_password`` guards that, but ``change_password`` called
    ``bcrypt.verify`` bare (CC045 class).
    """
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "pw")
    _write_corrupt_hash(cred_path)
    with pytest.raises(CorruptCredentialsFile):
        creds.change_password("admin", "pw", "new-pw")


def test_change_username_corrupt_hash_raises_typed_error(cred_path: Path) -> None:
    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "pw")
    _write_corrupt_hash(cred_path)
    with pytest.raises(CorruptCredentialsFile):
        creds.change_username("admin", "pw", "newname")


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


def test_mutations_serialize_on_a_cross_process_file_lock(cred_path: Path) -> None:
    """RMW mutators block on the sidecar flock, not just the in-process lock.

    With uvicorn_workers > 1 each worker holds its own CredentialsFile and
    its own threading.Lock, so a touch_api_key rewrite in one process could
    clobber a concurrent bump_session_epoch in another (the lost bump kept
    a logged-out cookie valid until TTL). Holding the sidecar flock from
    "another process" (a separate handle here) must block every mutator
    until release.
    """
    import threading
    import time

    from chaoscypher_core.utils.filelock import lock_file, unlock_file

    creds = CredentialsFile(cred_path)
    creds.initialize("admin", "CorrectHorse9!")
    epoch_before = creds.get_session_epoch()

    lock_path = cred_path.with_suffix(cred_path.suffix + ".lock")
    done: list[str] = []

    with open(lock_path, "w", encoding="utf-8") as foreign_holder:
        lock_file(foreign_holder, blocking=True)
        worker = threading.Thread(
            target=lambda: (creds.bump_session_epoch(), done.append("bumped"))
        )
        worker.start()
        time.sleep(0.2)
        assert not done, "mutator proceeded while a foreign process held the file lock"
        unlock_file(foreign_holder)
        worker.join(timeout=5)

    assert done == ["bumped"]
    assert creds.get_session_epoch() == epoch_before + 1
