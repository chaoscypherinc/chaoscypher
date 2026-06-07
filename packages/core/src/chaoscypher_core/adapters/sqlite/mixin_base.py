# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Base class for SQLite adapter mixins.

Declares the shared interface (attributes and methods) that all SQLite
adapter mixins access at runtime through the composed SqliteAdapter class.
These are provided by SqliteAdapter.__init__().
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chaoscypher_core.adapters.sqlite.utils import entities_to_dicts, entity_to_dict


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession


class SqliteMixinBase:
    """Base class for SQLite adapter mixins.

    Provides type declarations for attributes and methods shared across
    all mixins. These are implemented by SqliteAdapter at composition time.

    Entity conversion utilities are provided as static methods that
    delegate to the module-level functions in utils.py.

    This class exists solely for mypy type-checking support.
    It is never instantiated directly.
    """

    database_name: str
    # ``None`` is briefly possible during the connect/disconnect window;
    # post-connect mixin call sites assume non-None (every method calls
    # ``self._ensure_connected()`` first or is invoked only when connected).
    session: SafeSession | None
    db_path: Path
    _connected: bool

    def _ensure_connected(self) -> None:
        """Ensure adapter is connected (implemented by SqliteAdapter)."""

    def _maybe_commit(self) -> None:
        """Commit or flush via the shared SafeSession coordinator.

        All writers sharing a session participate in the same transaction
        boundary. ``SafeSession.maybe_commit()`` reads the session's
        transaction depth and flushes inside an ``adapter.transaction()``
        context, commits otherwise. Falls back to a plain ``commit()``
        for test stubs that use a bare ``Session`` without ``SafeSession``.
        """
        session = self.session
        assert session is not None, "_maybe_commit called before connect() / after disconnect()"
        maybe_commit = getattr(session, "maybe_commit", None)
        if maybe_commit is not None:
            maybe_commit()
        else:
            # Intentional SafeSession fallback for test stubs that pass a bare
            # Session instead of a SafeSession wrapper. The only CC011 carve-out
            # outside adapters/sqlite/repos/*.py — see CANONICAL_PATTERNS.md.
            session.commit()  # noqa: CC011

    @staticmethod
    def _entity_to_dict(entity: Any) -> dict[str, Any] | None:
        """Convert entity to dict."""
        return entity_to_dict(entity)

    @staticmethod
    def _entities_to_dicts(entities: list[Any]) -> list[dict[str, Any]]:
        """Convert entities to dicts."""
        return entities_to_dicts(entities)
