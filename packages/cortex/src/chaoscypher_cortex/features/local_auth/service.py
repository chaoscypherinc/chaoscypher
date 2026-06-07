# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""LocalAuthService — orchestrates credentials + session cookies + API keys."""

from __future__ import annotations

from chaoscypher_core.services.local_auth import (
    API_KEY_PREFIX,
    CredentialsFile,
    InvalidPassword,
    InvalidSessionCookie,
    UsernameMismatch,
    decode_session,
    encode_session,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)
from chaoscypher_cortex.features.local_auth.models import (
    ApiKeyCreateResponse,
    ApiKeyListItem,
    AuthStatusResponse,
)


class LocalAuthService:
    """Orchestrator for the single-user local-auth system.

    Consumes:
      - ``CredentialsFile`` for durable password + API-key storage
      - session encode/decode for stateless HMAC cookies
      - api_keys generate/hash/verify for key material

    Framework-agnostic — no FastAPI concerns leak in here.
    """

    def __init__(
        self,
        credentials: CredentialsFile,
        session_secret: bytes,
        cookie_ttl_seconds: int,
    ) -> None:
        """Bind the service to its collaborators.

        Args:
            credentials: Durable credential/API-key store.
            session_secret: HMAC secret used to sign session cookies (32+ bytes).
            cookie_ttl_seconds: Session cookie lifetime.

        """
        self._creds = credentials
        self._secret = session_secret
        self._ttl = cookie_ttl_seconds

    def status(self, session_cookie: str | None) -> AuthStatusResponse:
        """Return current auth state: setup needed? authenticated? who?"""
        if not self._creds.is_initialized():
            return AuthStatusResponse(setup_needed=True, authenticated=False)
        if session_cookie is None:
            return AuthStatusResponse(setup_needed=False, authenticated=False)
        try:
            username = self.verify_session_cookie(session_cookie)
        except InvalidSessionCookie:
            return AuthStatusResponse(setup_needed=False, authenticated=False)
        return AuthStatusResponse(setup_needed=False, authenticated=True, username=username)

    def setup(self, username: str, password: str) -> str:
        """Initialize credentials file (first run) and return a session cookie.

        Args:
            username: Admin username.
            password: Plaintext admin password.

        Returns:
            Signed session cookie for the newly-created admin.

        Raises:
            FileExistsError: Credentials are already initialized.

        """
        self._creds.initialize(username, password)
        return self._issue_cookie(username)

    def login(self, username: str, password: str) -> str:
        """Validate credentials and return a new session cookie.

        Args:
            username: Supplied username.
            password: Supplied plaintext password.

        Returns:
            Signed session cookie on success.

        Raises:
            UsernameMismatch: The provided username does not match the stored user.
            InvalidPassword: The password does not match the stored hash.

        """
        if not self._creds.verify_password(username, password):
            if self._creds.get_username() != username:
                raise UsernameMismatch(username)
            raise InvalidPassword
        return self._issue_cookie(username)

    def verify_session_cookie(self, cookie: str) -> str:
        """Return username or raise InvalidSessionCookie.

        Validates signature + expiry (in ``decode_session``) and additionally
        checks that the cookie's session_epoch and username still match the
        credentials file — so password or username changes invalidate
        outstanding cookies.

        Args:
            cookie: Session cookie value from the client.

        Returns:
            The authenticated username.

        Raises:
            InvalidSessionCookie: Signature, expiry, epoch, or username mismatch.

        """
        payload = decode_session(cookie, secret=self._secret)
        current_epoch = self._creds.get_session_epoch()
        if payload.session_epoch != current_epoch:
            raise InvalidSessionCookie("stale epoch")
        if payload.username != self._creds.get_username():
            raise InvalidSessionCookie("user changed")
        return payload.username

    def verify_api_key(self, key: str) -> str | None:
        """Return the matching key id, or ``None``. Updates ``last_used_at`` on hit.

        Args:
            key: Plaintext API key from the client.

        Returns:
            The key id if one of the stored hashes matches; otherwise ``None``.

        """
        if not key.startswith(API_KEY_PREFIX):
            return None
        for key_id, hashed in self._creds.get_api_key_hashes():
            if verify_api_key(key, hashed):
                self._creds.touch_api_key(key_id)
                return key_id
        return None

    def change_password(self, username: str, old_password: str, new_password: str) -> None:
        """Rotate the admin password (bumps session epoch, invalidates cookies).

        Args:
            username: Current username.
            old_password: Current plaintext password.
            new_password: Replacement plaintext password.

        """
        self._creds.change_password(username, old_password, new_password)

    def change_username(self, old_username: str, password: str, new_username: str) -> str:
        """Rename the admin account and return a fresh cookie for the new name.

        Args:
            old_username: Current username.
            password: Current plaintext password.
            new_username: Replacement username.

        Returns:
            A freshly-signed cookie bound to ``new_username``.

        """
        self._creds.change_username(old_username, password, new_username)
        return self._issue_cookie(new_username)

    def create_api_key(self, name: str) -> ApiKeyCreateResponse:
        """Mint a new API key and return the plaintext value (shown ONCE).

        Args:
            name: Human-readable label for the key.

        Returns:
            Response DTO containing id, name, plaintext key, and ``created_at``.

        """
        key = generate_api_key()
        key_id = self._creds.add_api_key(name, hash_api_key(key))
        record = next(rec for rec in self._creds.list_api_keys() if rec["id"] == key_id)
        return ApiKeyCreateResponse(id=key_id, name=name, key=key, created_at=record["created_at"])

    def list_api_keys(self) -> list[ApiKeyListItem]:
        """Return a safe listing of API keys (no hashes, no plaintext)."""
        return [
            ApiKeyListItem(
                id=rec["id"],
                name=rec["name"],
                created_at=rec["created_at"],
                last_used_at=rec["last_used_at"],
            )
            for rec in self._creds.list_api_keys()
        ]

    def logout(self) -> None:
        """Invalidate every outstanding session cookie by bumping the epoch."""
        self._creds.bump_session_epoch()

    def revoke_api_key(self, key_id: str) -> None:
        """Remove the API key with the given id.

        Args:
            key_id: Id returned by :meth:`create_api_key`.

        Raises:
            ApiKeyNotFound: No key with that id exists.

        """
        self._creds.revoke_api_key(key_id)

    def get_username(self) -> str:
        """Return the current stored username (used by the API-key auth path)."""
        return self._creds.get_username()

    def _issue_cookie(self, username: str) -> str:
        """Sign and return a session cookie for ``username`` at the current epoch."""
        return encode_session(
            username=username,
            session_epoch=self._creds.get_session_epoch(),
            ttl_seconds=self._ttl,
            secret=self._secret,
        )
