# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Exception-contract tests for GraphCleanupService.cleanup_orphaned_items.

The RuntimeError at graph_cleanup.py:81 is an internal programmer-error
guard that fires if the SQLite adapter's session is None inside the
transaction() context manager — a state that indicates a broken adapter
implementation, not a recoverable user-visible failure.  The noqa suppression
documents this intent and keeps the stdlib exception here by design.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.reset.graph_cleanup import GraphCleanupService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _null_session_adapter() -> MagicMock:
    """Return a mock adapter whose session is always None.

    This simulates the broken-adapter edge case that the guard at line 81
    is designed to catch.
    """
    adapter = MagicMock()
    adapter.session = None

    @contextmanager
    def _transaction() -> Iterator[None]:
        yield

    adapter.transaction = _transaction
    return adapter


# ---------------------------------------------------------------------------
# graph_cleanup.py:81 — programmer-error guard when adapter.session is None
#
# This RuntimeError is intentionally kept as stdlib (noqa: CC045) because
# it signals an internal invariant violation, not a user-visible operation
# failure.  It should never fire in production; if it does, the adapter is
# broken, not the user's input.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGraphCleanupNullSessionGuard:
    """RuntimeError is raised when adapter.session is None inside transaction().

    This is a programmer-error guard (internal invariant), not a user-visible
    failure.  The test pins the behaviour so refactoring cannot silently
    swallow the error.
    """

    def test_raises_runtime_error_when_session_is_none(self) -> None:
        service = GraphCleanupService(database_name="test_db")
        mock_adapter = _null_session_adapter()

        with patch(
            "chaoscypher_core.services.reset.graph_cleanup.get_sqlite_adapter",
            return_value=mock_adapter,
        ):
            with pytest.raises(RuntimeError, match="Adapter session is None"):
                service.cleanup_orphaned_items()

    def test_runtime_error_message_is_descriptive(self) -> None:
        service = GraphCleanupService(database_name="test_db")
        mock_adapter = _null_session_adapter()

        with patch(
            "chaoscypher_core.services.reset.graph_cleanup.get_sqlite_adapter",
            return_value=mock_adapter,
        ):
            with pytest.raises(RuntimeError) as exc_info:
                service.cleanup_orphaned_items()

        assert "transaction" in str(exc_info.value).lower()
