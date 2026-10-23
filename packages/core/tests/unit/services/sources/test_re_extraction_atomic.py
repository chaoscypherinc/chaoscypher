# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: force-re-extract must roll back the graph release if reset fails."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.models import ReleasedRows
from chaoscypher_core.services.sources.management.re_extraction import (
    force_re_extract,
)


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """The repository force_re_extract builds on the adapter's session; records the build."""
    repo = MagicMock()
    repo.release_source.return_value = ReleasedRows(deleted_node_ids=frozenset({"n1", "n2"}))
    repo.built_with = []

    def _build(adapter: object, database_name: str) -> MagicMock:
        repo.built_with.append((adapter, database_name))
        return repo

    monkeypatch.setattr(
        "chaoscypher_core.services.sources.management.re_extraction.get_transaction_graph_repository",
        _build,
    )
    return repo


def test_graph_release_rolled_back_when_reset_fails(graph: MagicMock) -> None:
    storage_adapter = MagicMock()
    storage_adapter.reset_for_re_extraction.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        force_re_extract(
            source_id="src_1",
            database_name="default",
            storage_adapter=storage_adapter,
            settings=None,
        )

    assert storage_adapter.transaction.called
    storage_adapter.reset_for_re_extraction.assert_called_once()
    # Confirm __exit__ saw the exception (so the CM had the chance to roll back).
    exit_call = storage_adapter.transaction.return_value.__exit__.call_args
    assert exit_call[0][0] is RuntimeError


def test_graph_release_runs_on_the_adapters_session(graph: MagicMock) -> None:
    """The release must run on the adapter's session so transaction() actually rolls it back.

    This is the difference between 'documented atomicity' and 'real atomicity':
    the repository is built from the adapter, never taken from the caller.
    """
    storage_adapter = MagicMock()

    force_re_extract(
        source_id="src_1", database_name="default", storage_adapter=storage_adapter, settings=None
    )

    assert graph.built_with == [(storage_adapter, "default")]


def test_happy_path_calls_both_in_order(graph: MagicMock) -> None:
    storage_adapter = MagicMock()
    order: list[str] = []
    graph.release_source.side_effect = lambda sid: (
        order.append("release") or ReleasedRows(deleted_node_ids=frozenset({"n1", "n2"}))
    )
    storage_adapter.reset_for_re_extraction.side_effect = lambda **_: order.append("reset")

    result = force_re_extract(
        source_id="src_1",
        database_name="default",
        storage_adapter=storage_adapter,
        settings=None,
    )

    graph.release_source.assert_called_once_with("src_1")
    storage_adapter.reset_for_re_extraction.assert_called_once_with(
        source_id="src_1", database_name="default"
    )
    assert order == ["release", "reset"]
    assert result.deleted_node_ids == {"n1", "n2"}


def test_raises_when_adapter_session_is_none() -> None:
    storage_adapter = MagicMock()
    storage_adapter.session = None

    with pytest.raises(RuntimeError, match="connected adapter"):
        force_re_extract(
            source_id="src_1",
            database_name="default",
            storage_adapter=storage_adapter,
            settings=None,
        )

    # Neither write should have been attempted.
    storage_adapter.reset_for_re_extraction.assert_not_called()
