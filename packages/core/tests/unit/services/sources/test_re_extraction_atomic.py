# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: force-re-extract must roll back graph delete if reset fails."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.management.re_extraction import (
    force_re_extract,
)


def test_graph_delete_rolled_back_when_reset_fails() -> None:
    storage_adapter = MagicMock()
    graph_repository = MagicMock()

    storage_adapter.reset_for_re_extraction.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        force_re_extract(
            source_id="src_1",
            database_name="default",
            storage_adapter=storage_adapter,
            graph_repository=graph_repository,
        )

    assert storage_adapter.transaction.called
    storage_adapter.reset_for_re_extraction.assert_called_once()
    # Confirm __exit__ saw the exception (so the CM had the chance to roll back).
    exit_call = storage_adapter.transaction.return_value.__exit__.call_args
    assert exit_call[0][0] is RuntimeError


def test_graph_delete_uses_shared_session() -> None:
    """The graph delete must run on the adapter's session so transaction()
    actually rolls it back. This is the difference between 'documented
    atomicity' and 'real atomicity'.
    """
    storage_adapter = MagicMock()
    sentinel_session = MagicMock(name="adapter_session")
    storage_adapter.session = sentinel_session
    storage_adapter.transaction.return_value.__enter__ = MagicMock(return_value=None)
    storage_adapter.transaction.return_value.__exit__ = MagicMock(return_value=None)
    graph_repository = MagicMock()

    force_re_extract(
        source_id="src_1",
        database_name="default",
        storage_adapter=storage_adapter,
        graph_repository=graph_repository,
    )

    # Confirm the graph delete received the adapter's session.
    call_kwargs = graph_repository.delete_source_artifacts.call_args.kwargs
    assert call_kwargs.get("session") is sentinel_session


def test_happy_path_calls_both_in_order() -> None:
    storage_adapter = MagicMock()
    graph_repository = MagicMock()
    storage_adapter.transaction.return_value.__enter__ = MagicMock(return_value=None)
    storage_adapter.transaction.return_value.__exit__ = MagicMock(return_value=None)
    graph_repository.delete_source_artifacts.return_value = {
        "nodes_deleted": 2,
        "edges_deleted": 1,
        "templates_deleted": 0,
    }

    result = force_re_extract(
        source_id="src_1",
        database_name="default",
        storage_adapter=storage_adapter,
        graph_repository=graph_repository,
    )

    # delete_source_artifacts is called with the positional source_id and the
    # adapter's session so all three SQL deletes share the adapter transaction.
    graph_repository.delete_source_artifacts.assert_called_once_with(
        "src_1", session=storage_adapter.session
    )
    storage_adapter.reset_for_re_extraction.assert_called_once_with(
        source_id="src_1", database_name="default"
    )
    assert result["nodes_deleted"] == 2


def test_raises_when_adapter_session_is_none() -> None:
    storage_adapter = MagicMock()
    storage_adapter.session = None
    storage_adapter.transaction.return_value.__enter__ = MagicMock(return_value=None)
    storage_adapter.transaction.return_value.__exit__ = MagicMock(return_value=None)
    graph_repository = MagicMock()

    with pytest.raises(RuntimeError, match="connected adapter"):
        force_re_extract(
            source_id="src_1",
            database_name="default",
            storage_adapter=storage_adapter,
            graph_repository=graph_repository,
        )

    # Neither write should have been attempted.
    graph_repository.delete_source_artifacts.assert_not_called()
    storage_adapter.reset_for_re_extraction.assert_not_called()
