# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lexicon Credential Storage - Storage abstractions for lexicon credentials.

Provides a protocol and implementations for storing lexicon authentication
credentials. Different implementations support different use cases:

- FileLexiconStorage: File-based storage for CLI (persistent)
- DictLexiconStorage: In-memory storage for Cortex/testing

Example:
    from chaoscypher_core.services.lexicon.storage import FileLexiconStorage

    storage = FileLexiconStorage()
    creds = storage.load_credentials()
    if creds and creds.is_authenticated:
        print(f"Logged in as {creds.username}")
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog

from chaoscypher_core.services.lexicon.models import LexiconAuthConfig


logger = structlog.get_logger(__name__)


@runtime_checkable
class LexiconCredentialStorage(Protocol):
    """Protocol for lexicon credential storage.

    Defines the interface for storing and retrieving lexicon authentication
    credentials. Implementations can use files, databases, or in-memory
    storage depending on the use case.
    """

    def load_credentials(self) -> LexiconAuthConfig | None:
        """Load stored credentials.

        Returns:
            LexiconAuthConfig if credentials exist, None otherwise.
        """
        ...

    def save_credentials(self, lexicon_url: str, auth: LexiconAuthConfig) -> None:
        """Save credentials.

        Args:
            lexicon_url: Lexicon URL these credentials are for.
            auth: Authentication configuration to save.
        """
        ...

    def clear_credentials(self) -> None:
        """Clear stored credentials."""
        ...

    def get_lexicon_url(self) -> str:
        """Get configured lexicon URL.

        Returns:
            Lexicon URL (default if not configured).
        """
        ...


class FileLexiconStorage:
    """File-based credential storage.

    Stores lexicon login state in ``auth.json`` (``PathSettings.auth_file``)
    inside the user's config directory. Uses restrictive file permissions
    (0600) for security.

    Used by CLI for persistent authentication. There is no read-fallback to
    the pre-unification ``credentials.json`` (clean break — no compat shims).

    Attributes:
        config_dir: Directory for config files.
        auth_file: Path to the ``auth.json`` login-state file.

    Example:
        storage = FileLexiconStorage()
        storage.save_credentials(
            "https://lexicon.chaoscypher.io",
            LexiconAuthConfig(token="...", username="john")
        )
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        """Initialize file credential storage.

        Args:
            config_dir: Config directory path. Defaults to ~/.config/chaoscypher
        """
        self.config_dir = config_dir or Path.home() / ".config" / "chaoscypher"
        self.auth_file = self.config_dir / "auth.json"

    def _ensure_config_dir(self) -> None:
        """Ensure config directory exists with proper permissions."""
        if not self.config_dir.exists():
            self.config_dir.mkdir(parents=True, mode=0o700)
            logger.debug("config_dir_created", path=str(self.config_dir))

    def _set_file_permissions(self, path: Path) -> None:
        """Set restrictive permissions on credential file.

        Args:
            path: File path to secure.
        """
        # Only set permissions on Unix-like systems
        if os.name != "nt":
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600

    def load_credentials(self) -> LexiconAuthConfig | None:
        """Load credentials from ``auth.json``.

        Returns:
            LexiconAuthConfig if the file exists and is valid, None otherwise.
        """
        if not self.auth_file.exists():
            return None

        try:
            data = json.loads(self.auth_file.read_text(encoding="utf-8"))
            return LexiconAuthConfig(
                token=data.get("token"),
                refresh_token=data.get("refresh_token"),
                expires_at=data.get("expires_at"),
                username=data.get("username"),
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "credentials_load_failed",
                path=str(self.auth_file),
                error=str(e),
            )
            return None

    def save_credentials(self, lexicon_url: str, auth: LexiconAuthConfig) -> None:
        """Save credentials to ``auth.json``.

        Args:
            lexicon_url: Lexicon URL these credentials are for.
            auth: Authentication configuration to save.
        """
        self._ensure_config_dir()

        data = {
            "lexicon_url": lexicon_url,
            "token": auth.token.get_secret_value() if auth.token else None,
            "refresh_token": (
                auth.refresh_token.get_secret_value() if auth.refresh_token else None
            ),
            "expires_at": auth.expires_at,
            "username": auth.username,
        }

        self.auth_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._set_file_permissions(self.auth_file)

        logger.info(
            "credentials_saved",
            path=str(self.auth_file),
            username=auth.username,
        )

    def clear_credentials(self) -> None:
        """Clear credentials by deleting ``auth.json``."""
        if self.auth_file.exists():
            self.auth_file.unlink()
            logger.info("credentials_cleared", path=str(self.auth_file))

    def get_lexicon_url(self) -> str:
        """Get lexicon URL from stored credentials or settings default.

        Returns:
            Lexicon URL from ``auth.json``, falling back to
            ``settings.lexicon.url`` when no auth file exists or the file
            does not pin a URL.
        """
        from chaoscypher_core.app_config import get_settings

        default = get_settings().lexicon.url
        if self.auth_file.exists():
            try:
                data = json.loads(self.auth_file.read_text(encoding="utf-8"))
                url: str = data.get("lexicon_url", default)
                return url
            except json.JSONDecodeError, OSError:
                pass
        return default


class DictLexiconStorage:
    """Dictionary-based credential storage.

    Stores credentials in memory using a dictionary. Useful for:
    - Cortex (credentials from settings.yaml)
    - Testing (mock storage)

    Can be initialized with existing data from settings.

    Attributes:
        _data: Internal storage dictionary.

    Example:
        # Initialize from settings
        storage = DictLexiconStorage(settings.lexicon.model_dump())

        # Or empty for testing
        storage = DictLexiconStorage()
    """

    def __init__(
        self,
        initial_data: dict[str, Any] | None = None,
        *,
        on_save: Any | None = None,
    ) -> None:
        """Initialize dict credential storage.

        Args:
            initial_data: Initial credential data (e.g., from settings).
            on_save: Optional callback called when credentials are saved.
                     Signature: on_save(lexicon_url: str, auth: LexiconAuthConfig)
        """
        self._data: dict[str, Any] = dict(initial_data) if initial_data else {}
        self._on_save = on_save

    def load_credentials(self) -> LexiconAuthConfig | None:
        """Load credentials from internal dictionary.

        Returns:
            LexiconAuthConfig if token exists, None otherwise.
        """
        token = self._data.get("token")
        if not token:
            return None

        return LexiconAuthConfig(
            token=token,
            refresh_token=self._data.get("refresh_token"),
            expires_at=self._data.get("expires_at"),
            username=self._data.get("username"),
        )

    def save_credentials(self, lexicon_url: str, auth: LexiconAuthConfig) -> None:
        """Save credentials to internal dictionary.

        Args:
            lexicon_url: Lexicon URL these credentials are for.
            auth: Authentication configuration to save.
        """
        self._data["url"] = lexicon_url
        self._data["token"] = auth.token.get_secret_value() if auth.token else None
        self._data["refresh_token"] = (
            auth.refresh_token.get_secret_value() if auth.refresh_token else None
        )
        self._data["expires_at"] = auth.expires_at
        self._data["username"] = auth.username

        if self._on_save:
            self._on_save(lexicon_url, auth)

        logger.debug(
            "credentials_saved_to_dict",
            lexicon_url=lexicon_url,
            username=auth.username,
        )

    def clear_credentials(self) -> None:
        """Clear credentials from dictionary."""
        self._data.pop("token", None)
        self._data.pop("refresh_token", None)
        self._data.pop("expires_at", None)
        self._data.pop("username", None)
        logger.debug("credentials_cleared_from_dict")

    def get_lexicon_url(self) -> str:
        """Get lexicon URL from dictionary or settings default.

        Returns:
            Lexicon URL from data, falling back to ``settings.lexicon.url``.
        """
        from chaoscypher_core.app_config import get_settings

        url: str = self._data.get("url", get_settings().lexicon.url)
        return url


__all__ = [
    "DictLexiconStorage",
    "FileLexiconStorage",
    "LexiconCredentialStorage",
]
