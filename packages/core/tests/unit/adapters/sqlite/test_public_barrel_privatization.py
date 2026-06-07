# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 3 Task I: SafeSession + session helpers removed from public barrels.

``SafeSession`` is adapter-internal retry infrastructure, not a DTO-shaped
collaborator that services should type-hint against. The session helpers
(``get_session`` / ``get_db_session`` / ``get_engine``) belong inside the
adapter for the same reason: callers should route through
``SqliteAdapter.transaction()`` rather than reaching past it.

Phase 3 keeps the implementations where they are (so adapter-internal
consumers still work) but trims them out of the two public barrels:

* ``chaoscypher_core.adapters.sqlite`` no longer re-exports SafeSession
* ``chaoscypher_core.adapters`` no longer re-exports
  ``get_session`` / ``get_db_session`` / ``get_engine``

The class and helpers remain importable from their submodules for
adapter-internal use — a separate refactor (Phase 4 Cortex cleanup)
will migrate the remaining Cortex consumers to
``SqliteAdapter.transaction()``.
"""

from __future__ import annotations


def test_sqlite_barrel_does_not_export_safe_session() -> None:
    import chaoscypher_core.adapters.sqlite as sqlite_barrel

    assert "SafeSession" not in sqlite_barrel.__all__, (
        "SafeSession must not appear in chaoscypher_core.adapters.sqlite.__all__ — "
        "it is adapter-internal."
    )


def test_adapters_barrel_does_not_export_session_helpers() -> None:
    import chaoscypher_core.adapters as adapters_barrel

    for name in ("get_session", "get_db_session", "get_engine"):
        assert name not in adapters_barrel.__all__, (
            f"{name} must not appear in chaoscypher_core.adapters.__all__ — "
            f"route through SqliteAdapter.transaction() instead."
        )


def test_safe_session_still_importable_from_canonical_submodule() -> None:
    """Removing from the barrel should not break legitimate adapter-internal consumers."""
    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession

    assert SafeSession.__name__ == "SafeSession"


def test_session_helpers_still_importable_from_canonical_submodule() -> None:
    from chaoscypher_core.adapters.sqlite.engine import get_engine
    from chaoscypher_core.adapters.sqlite.session import get_db_session, get_session

    assert callable(get_engine)
    assert callable(get_session)
    assert callable(get_db_session)
