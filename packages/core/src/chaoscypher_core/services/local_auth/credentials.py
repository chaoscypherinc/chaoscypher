# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Credentials file storage: password hash + API key hashes in a single JSON file.

Single-user: one password hash, many API keys. Never stores plaintext.
Atomic writes via tempfile + os.replace so the file is never partially written.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from passlib.hash import bcrypt  # type: ignore[import-untyped]

from chaoscypher_core.services.local_auth.errors import (
    ApiKeyNotFound,
    CorruptCredentialsFile,
    CredentialsNotInitialized,
    InvalidPassword,
    UsernameMismatch,
)


class ApiKeyRecord(TypedDict):
    """Persisted API key record (includes hash)."""

    id: str
    name: str
    hash: str
    created_at: str
    last_used_at: str | None


class UserRecord(TypedDict):
    """Persisted user record (username + password hash)."""

    username: str
    password_hash: str


class CredentialsData(TypedDict):
    """Full on-disk shape of the credentials file."""

    user: UserRecord
    api_keys: list[ApiKeyRecord]
    session_epoch: int


BCRYPT_ROUNDS = 12


class CredentialsFile:
    """Flat-file credential store for single-user local deployments.

    Holds one user (username + bcrypt password hash), a list of hashed API
    keys, and a monotonically increasing ``session_epoch`` used to invalidate
    outstanding session cookies when the password or username changes.

    All writes go through :meth:`_atomic_write`, which writes a sibling
    tempfile, chmods it to ``0600`` on POSIX, then renames it over the target
    so a partial write can never leave a corrupt credentials file on disk.
    """

    def __init__(self, path: Path) -> None:
        """Bind this store to a specific file path.

        Args:
            path: Location of ``credentials.json`` on disk.

        """
        self._path = path
        self._lock = threading.Lock()

    def is_initialized(self) -> bool:
        """Return True if the credentials file exists on disk."""
        return self._path.exists()

    def initialize(self, username: str, password: str) -> None:
        """Create a fresh credentials file for a first-run admin.

        Args:
            username: Admin username.
            password: Plaintext password; hashed with bcrypt before storage.

        Raises:
            FileExistsError: If the credentials file already exists.

        """
        with self._lock:
            if self._path.exists():
                msg = f"Credentials already initialized at {self._path}"
                raise FileExistsError(msg)
            data: CredentialsData = {
                "user": {
                    "username": username,
                    "password_hash": bcrypt.using(rounds=BCRYPT_ROUNDS).hash(password),
                },
                "api_keys": [],
                "session_epoch": 1,
            }
            self._atomic_write(data)

    def verify_password(self, username: str, password: str) -> bool:
        """Return True if ``(username, password)`` match the stored credentials.

        Args:
            username: Supplied username.
            password: Supplied plaintext password.

        Returns:
            True on exact match, False otherwise. Never raises on a wrong
            password — only on an uninitialized store.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        data = self._load()
        if data["user"]["username"] != username:
            return False
        try:
            return bool(bcrypt.verify(password, data["user"]["password_hash"]))
        except ValueError:
            return False

    def change_password(self, username: str, old_password: str, new_password: str) -> None:
        """Rotate the stored password and bump the session epoch.

        Args:
            username: Current username (must match stored user).
            old_password: Current plaintext password (verified against hash).
            new_password: Replacement plaintext password.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.
            UsernameMismatch: If ``username`` does not match the stored user.
            InvalidPassword: If ``old_password`` does not verify.

        """
        with self._lock:
            data = self._load()
            if data["user"]["username"] != username:
                raise UsernameMismatch(username)
            if not bcrypt.verify(old_password, data["user"]["password_hash"]):
                raise InvalidPassword
            data["user"]["password_hash"] = bcrypt.using(rounds=BCRYPT_ROUNDS).hash(new_password)
            data["session_epoch"] += 1
            self._atomic_write(data)

    def change_username(self, old_username: str, password: str, new_username: str) -> None:
        """Rename the stored user and bump the session epoch.

        Args:
            old_username: Current username (must match stored user).
            password: Current plaintext password (verified against hash).
            new_username: Replacement username.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.
            UsernameMismatch: If ``old_username`` does not match the stored user.
            InvalidPassword: If ``password`` does not verify.

        """
        with self._lock:
            data = self._load()
            if data["user"]["username"] != old_username:
                raise UsernameMismatch(old_username)
            if not bcrypt.verify(password, data["user"]["password_hash"]):
                raise InvalidPassword
            data["user"]["username"] = new_username
            data["session_epoch"] += 1
            self._atomic_write(data)

    def bump_session_epoch(self) -> None:
        """Increment the session epoch, invalidating every outstanding cookie.

        Called on logout so a stolen cookie cannot be replayed even before
        its TTL expires.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        with self._lock:
            data = self._load()
            data["session_epoch"] = int(data.get("session_epoch", 0)) + 1
            self._atomic_write(data)

    def get_username(self) -> str:
        """Return the stored username.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        return self._load()["user"]["username"]

    def get_session_epoch(self) -> int:
        """Return the current session epoch (bumped on password/username change).

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        return self._load()["session_epoch"]

    def add_api_key(self, name: str, key_hash: str) -> str:
        """Append a new API key record and return its generated id.

        Args:
            name: Human-readable label for the key.
            key_hash: Pre-hashed secret (callers hash before calling).

        Returns:
            The generated key id (e.g. ``k_ab12...``).

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        with self._lock:
            data = self._load()
            key_id = f"k_{secrets.token_hex(8)}"
            record: ApiKeyRecord = {
                "id": key_id,
                "name": name,
                "hash": key_hash,
                "created_at": datetime.now(UTC).isoformat(),
                "last_used_at": None,
            }
            data["api_keys"].append(record)
            self._atomic_write(data)
            return key_id

    def list_api_keys(self) -> list[ApiKeyRecord]:
        """Return API key records with the ``hash`` field blanked out.

        Safe to return to callers/UI — no secret material is leaked.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        data = self._load()
        return [{**rec, "hash": ""} for rec in data["api_keys"]]

    def get_api_key_hashes(self) -> list[tuple[str, str]]:
        """Return ``(id, hash)`` pairs for internal verification use only.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        data = self._load()
        return [(rec["id"], rec["hash"]) for rec in data["api_keys"]]

    def revoke_api_key(self, key_id: str) -> None:
        """Remove the API key with the given id.

        Args:
            key_id: Id returned by :meth:`add_api_key`.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.
            ApiKeyNotFound: If no key with ``key_id`` exists.

        """
        with self._lock:
            data = self._load()
            before = len(data["api_keys"])
            data["api_keys"] = [rec for rec in data["api_keys"] if rec["id"] != key_id]
            if len(data["api_keys"]) == before:
                raise ApiKeyNotFound(key_id)
            self._atomic_write(data)

    def touch_api_key(self, key_id: str) -> None:
        """Update ``last_used_at`` for a key. Silent no-op if the key is missing.

        Args:
            key_id: Id returned by :meth:`add_api_key`.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        with self._lock:
            data = self._load()
            for rec in data["api_keys"]:
                if rec["id"] == key_id:
                    rec["last_used_at"] = datetime.now(UTC).isoformat()
                    self._atomic_write(data)
                    return

    def _load(self) -> CredentialsData:
        """Read and parse the on-disk credentials file.

        Raises:
            CredentialsNotInitialized: If the file does not exist.
            CorruptCredentialsFile: If the file exists but is not valid JSON.

        """
        if not self._path.exists():
            raise CredentialsNotInitialized(str(self._path))
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except json.JSONDecodeError as exc:
            raise CorruptCredentialsFile(str(self._path)) from exc

    def _atomic_write(self, data: CredentialsData) -> None:
        """Write ``data`` atomically: tempfile -> chmod 0600 -> os.replace."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=".credentials_", suffix=".json", dir=str(self._path.parent)
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            if os.name == "posix":
                tmp_path.chmod(0o600)
            # ``os.replace`` is intentional here: tests monkeypatch it to
            # simulate a failed rename, and swapping it for ``Path.replace``
            # would bypass those patches (``Path.replace`` calls the
            # already-resolved C-level replacement).
            os.replace(tmp_path, self._path)  # noqa: PTH105 — see comment above
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
