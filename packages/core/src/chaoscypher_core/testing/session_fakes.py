# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Fake storage sessions for tests that drive code which opens ``session_scope()``.

Test infrastructure only (see this package's docstring). A ``MagicMock``
storage adapter cannot stand in for ``session_scope()`` on its own: the
``async with`` makes the session an ``AsyncMock``, whose ``execute`` returns a
coroutine, so a backfill that reads rows (``recompute_chunk_sentence_offsets``,
run by every reindex since audit wave 3) breaks on ``.fetchall()``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


def empty_session_scope() -> MagicMock:
    """A ``session_scope()`` whose session answers every query with no rows.

    Assign it as the adapter's ``session_scope`` (``adapter.session_scope =
    empty_session_scope``): the block runs, nothing is read, nothing is
    committed.

    Returns:
        An async context manager yielding a synchronous ``MagicMock`` session.
    """
    scope = MagicMock()
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = []
    scope.__aenter__ = AsyncMock(return_value=session)
    scope.__aexit__ = AsyncMock(return_value=False)
    return scope


__all__ = ["empty_session_scope"]
