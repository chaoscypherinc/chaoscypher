# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Credentials file storage: password hash + API key hashes in a single JSON file.

Single-user: one password hash, many API keys. Never stores plaintext.
Atomic writes via tempfile + os.replace so the file is never partially written.

API-key records also carry a ``selector`` — a keyed HMAC of the plaintext key
(see ``api_keys.compute_api_key_selector``) — used as a lookup index so that
verification bcrypt-checks one candidate instead of every stored hash. The
HMAC secret lives in this same file as ``api_key_selector_secret``, minted on
first need. It is only an index: a leaked credentials file reveals no key
material, because bcrypt remains the authenticator.

Records written before selectors existed simply lack the field; they are
transparently migrated on their first successful verify (see
``api_key_lookup``). There is no file-format version stamp and none is needed
— both readers and writers treat ``selector`` as optional.
"""

from __future__ import annotations

import json
import secrets
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NotRequired, TypedDict

import structlog
from passlib.hash import bcrypt  # type: ignore[import-untyped]

from chaoscypher_core.services.local_auth.api_keys import generate_selector_secret
from chaoscypher_core.services.local_auth.errors import (
    ApiKeyNotFound,
    CorruptCredentialsFile,
    CredentialsAlreadyInitialized,
    CredentialsNotInitialized,
    InvalidPassword,
    UsernameMismatch,
)
from chaoscypher_core.utils.filelock import lock_file, unlock_file
from chaoscypher_core.utils.secure_write import atomic_secret_write


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class ApiKeyRecord(TypedDict):
    """Persisted API key record (includes hash).

    ``selector`` is absent on records written before keyed-selector lookup
    landed; those are migrated on first successful verify.
    """

    id: str
    name: str
    hash: str
    created_at: str
    last_used_at: str | None
    selector: NotRequired[str]


class UserRecord(TypedDict):
    """Persisted user record (username + password hash)."""

    username: str
    password_hash: str


class CredentialsData(TypedDict):
    """Full on-disk shape of the credentials file.

    ``api_key_selector_secret`` is absent until the first API key is minted
    or verified on an install that has the selector index.
    """

    user: UserRecord
    api_keys: list[ApiKeyRecord]
    session_epoch: int
    api_key_selector_secret: NotRequired[str]


logger = structlog.get_logger(__name__)

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

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize a read-modify-write cycle across threads AND processes.

        ``_atomic_write`` makes each single write atomic, but every mutator
        is load -> mutate -> write; the in-process ``threading.Lock`` alone
        left that pair unserialized across uvicorn workers (supported up to
        8), where a ``touch_api_key`` rewrite in one worker silently
        clobbered a concurrent ``bump_session_epoch`` in another — keeping
        the cookie that logout was supposed to invalidate valid until its
        TTL. Matches the cross-process flock precedent of
        ``database/engine.py`` (``.init.lock``) and ``cortex/lifespan.py``.
        The sidecar lock file is deliberately never unlinked: flock binds
        to the inode, and recycling the path hands a fresh, instantly
        lockable inode to the next process.
        """
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self._path.with_suffix(self._path.suffix + ".lock")
            with open(lock_path, "w", encoding="utf-8") as handle:
                lock_file(handle, blocking=True)
                try:
                    yield
                finally:
                    unlock_file(handle)

    def is_initialized(self) -> bool:
        """Return True if the credentials file exists on disk."""
        return self._path.exists()

    def initialize(self, username: str, password: str) -> None:
        """Create a fresh credentials file for a first-run admin.

        Args:
            username: Admin username.
            password: Plaintext password; hashed with bcrypt before storage.

        Raises:
            CredentialsAlreadyInitialized: If the credentials file already exists.

        """
        with self._locked():
            if self._path.exists():
                msg = f"Credentials already initialized at {self._path}"
                raise CredentialsAlreadyInitialized(msg)
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
            CorruptCredentialsFile: If the stored hash is malformed.
            UsernameMismatch: If ``username`` does not match the stored user.
            InvalidPassword: If ``old_password`` does not verify.

        """
        with self._locked():
            data = self._load()
            if data["user"]["username"] != username:
                raise UsernameMismatch(username)
            if not self._verify_stored_hash(old_password, data["user"]["password_hash"]):
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
            CorruptCredentialsFile: If the stored hash is malformed.
            UsernameMismatch: If ``old_username`` does not match the stored user.
            InvalidPassword: If ``password`` does not verify.

        """
        with self._locked():
            data = self._load()
            if data["user"]["username"] != old_username:
                raise UsernameMismatch(old_username)
            if not self._verify_stored_hash(password, data["user"]["password_hash"]):
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
        with self._locked():
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

    def add_api_key(self, name: str, key_hash: str, *, selector: str | None = None) -> str:
        """Append a new API key record and return its generated id.

        Args:
            name: Human-readable label for the key.
            key_hash: Pre-hashed secret (callers hash before calling).
            selector: Optional keyed lookup selector for the same key (see
                ``api_keys.compute_api_key_selector``). Omitting it writes a
                selector-less record, which verifies through the migration
                loop — kept optional so existing callers keep working.

        Returns:
            The generated key id (e.g. ``k_ab12...``).

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        with self._locked():
            data = self._load()
            key_id = f"k_{secrets.token_hex(8)}"
            record: ApiKeyRecord = {
                "id": key_id,
                "name": name,
                "hash": key_hash,
                "created_at": datetime.now(UTC).isoformat(),
                "last_used_at": None,
            }
            if selector is not None:
                record["selector"] = selector
            data["api_keys"].append(record)
            self._atomic_write(data)
            return key_id

    def get_or_create_api_key_selector_secret(self) -> str:
        """Return the per-install selector secret, minting it on first need.

        The secret only keys the lookup index, never the authentication:
        every candidate still has to pass bcrypt. It lives in the credentials
        file so it is backed up, restored, and permissioned (0600) exactly
        like the hashes it indexes — losing one without the other would
        strand the keys either way.

        Returns:
            Hex-encoded secret, 64 characters.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.
            CorruptCredentialsFile: If the stored secret is not valid hex.
                Every bearer verify reaches this, so it must surface as a
                typed local-auth error rather than a bare stdlib exception
                from ``bytes.fromhex`` (CC045 class). The error names the
                file, never the secret.

        """
        existing = self._load().get("api_key_selector_secret")
        if existing:
            try:
                bytes.fromhex(existing)
            except ValueError as exc:
                self._log_corrupt("selector_secret_not_hex")
                raise CorruptCredentialsFile(str(self._path)) from exc
            return existing
        with self._locked():
            data = self._load()
            # Re-check inside the lock: another worker may have won the race.
            already = data.get("api_key_selector_secret")
            if already:
                return already
            secret = generate_selector_secret()
            data["api_key_selector_secret"] = secret
            self._atomic_write(data)
            return secret

    def find_api_key_by_selector(self, selector: str) -> tuple[str, str] | None:
        """Return ``(id, hash)`` for the record carrying ``selector``, or None.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        for rec in self._load()["api_keys"]:
            if rec.get("selector") == selector:
                return (rec["id"], rec["hash"])
        return None

    def get_legacy_api_key_hashes(self) -> list[tuple[str, str]]:
        """Return ``(id, hash)`` pairs for records that carry no selector.

        These are the only records the migration loop has to bcrypt-scan;
        the list drains to empty as each key is used once.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        return [
            (rec["id"], rec["hash"]) for rec in self._load()["api_keys"] if not rec.get("selector")
        ]

    def set_api_key_selector(self, key_id: str, selector: str) -> None:
        """Backfill the lookup selector on an existing record.

        Silent no-op if the key is missing (it may have been revoked between
        the verify and this write).

        Args:
            key_id: Id returned by :meth:`add_api_key`.
            selector: Keyed lookup selector for that key.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        with self._locked():
            data = self._load()
            for rec in data["api_keys"]:
                if rec["id"] == key_id:
                    rec["selector"] = selector
                    self._atomic_write(data)
                    return

    def list_api_keys(self) -> list[ApiKeyRecord]:
        """Return API key records with the ``hash`` and ``selector`` stripped.

        Safe to return to callers/UI — no secret material is leaked. The
        selector is only an index, but it is derived from the plaintext key,
        so it never leaves the storage layer either.

        Raises:
            CredentialsNotInitialized: If no credentials file exists.

        """
        data = self._load()
        return [
            {**{k: v for k, v in rec.items() if k != "selector"}, "hash": ""}  # type: ignore[typeddict-item]
            for rec in data["api_keys"]
        ]

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
        with self._locked():
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
        with self._locked():
            data = self._load()
            for rec in data["api_keys"]:
                if rec["id"] == key_id:
                    rec["last_used_at"] = datetime.now(UTC).isoformat()
                    self._atomic_write(data)
                    return

    def _verify_stored_hash(self, password: str, password_hash: str) -> bool:
        """Verify ``password`` against a stored bcrypt hash.

        Raises:
            CorruptCredentialsFile: If the stored hash is malformed.
                ``bcrypt.verify`` raises ``ValueError`` on a hash it cannot
                parse; that must surface as a typed local-auth error, not a
                bare stdlib exception (CC045 class).

        """
        try:
            return bool(bcrypt.verify(password, password_hash))
        except ValueError as exc:
            self._log_corrupt("unparseable_password_hash")
            raise CorruptCredentialsFile(str(self._path)) from exc

    def _load(self) -> CredentialsData:
        """Read and parse the on-disk credentials file.

        Raises:
            CredentialsNotInitialized: If the file does not exist.
            CorruptCredentialsFile: If the file exists but is not valid JSON.

        """
        if not self._path.exists():
            self._log_not_initialized()
            raise CredentialsNotInitialized(str(self._path))
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except json.JSONDecodeError as exc:
            self._log_corrupt("invalid_json")
            raise CorruptCredentialsFile(str(self._path)) from exc

    def _log_corrupt(self, reason: str) -> None:
        """Record the path of an unreadable credentials file, operator-side only.

        ``CorruptCredentialsFile`` deliberately carries no path in its message
        — it maps to a 401 whose body is rendered to the caller, and the
        unauthenticated bearer arm can reach it. The operator still needs to
        know which file to look at, so it goes here instead.
        """
        logger.error("corrupt_credentials_file", path=str(self._path), reason=reason)

    def _log_not_initialized(self) -> None:
        """Record the path of a missing credentials file, operator-side only.

        ``CredentialsNotInitialized`` deliberately carries no path in its
        message, for the same reason ``CorruptCredentialsFile`` does not: the
        message is rendered into a 401 body. This is the ordinary pre-setup
        state rather than a fault, so it logs at info level.
        """
        logger.info("credentials_not_initialized", path=str(self._path))

    def _atomic_write(self, data: CredentialsData) -> None:
        """Write ``data`` atomically: tempfile -> chmod 0600 -> os.replace."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_secret_write(self._path, json.dumps(data, indent=2), prefix=".credentials_")
