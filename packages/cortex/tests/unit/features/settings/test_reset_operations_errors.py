# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests guarding reset helpers against swallowing failures.

Before the 2026-04-18 critical fix, each helper caught Exception, set
its counters to 0, and allowed the caller to return
``{"status": "success", ...}`` at HTTP 200 - hiding real DB / FS
failures from operators. These tests assert the helpers now re-raise.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.reset import operations as reset_operations


class _BoomError(RuntimeError):
    """Sentinel exception raised from mocked dependencies."""


def _make_failing_session(error: Exception) -> MagicMock:
    """Build a context-managed session whose query call raises ``error``."""
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = None
    session.exec.side_effect = error
    session.commit.return_value = None
    return session


def test_delete_source_data_propagates_when_adapter_fails() -> None:
    """_delete_source_data must raise when an adapter method fails mid-delete."""
    stats: dict[str, object] = {}
    failing_adapter = MagicMock()
    failing_adapter.transaction.return_value.__enter__ = MagicMock(return_value=None)
    failing_adapter.transaction.return_value.__exit__ = MagicMock(return_value=None)
    # After the counts succeed, the first bulk delete blows up.
    failing_adapter.count_sources.return_value = 0
    failing_adapter.count_chunks.return_value = 0
    failing_adapter.clear_all_tag_assignments.side_effect = _BoomError(
        "simulated delete failure",
    )

    with (
        patch(
            "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
            return_value=failing_adapter,
        ),
        pytest.raises(_BoomError),
    ):
        reset_operations._delete_source_data("test_db", stats)


def test_reset_knowledge_graph_propagates_when_clear_all_fails() -> None:
    stats: dict[str, object] = {}

    with (
        patch(
            "chaoscypher_core.repo_factories.get_graph_repository",
            side_effect=_BoomError("graph repo init failed"),
        ),
        pytest.raises(_BoomError),
    ):
        reset_operations._reset_knowledge_graph("test_db", stats)


@pytest.mark.asyncio
async def test_delete_import_files_propagates_when_rmtree_fails() -> None:
    class _Paths:
        imports_subdir = "imports"

    class _Settings:
        database_dir = pathlib.Path("/tmp/does-not-exist-xyz")
        paths = _Paths()

    stats: dict[str, object] = {}

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("shutil.rmtree", side_effect=_BoomError("rmtree blew up")),
        pytest.raises(_BoomError),
    ):
        await reset_operations._delete_import_files(_Settings(), stats)


def test_reset_search_indices_propagates_when_clear_fails() -> None:
    stats: dict[str, object] = {}

    with (
        patch(
            "chaoscypher_core.repo_factories.get_search_repository",
            side_effect=_BoomError("search repo init failed"),
        ),
        pytest.raises(_BoomError),
    ):
        reset_operations._reset_search_indices("test_db", stats)


@pytest.mark.asyncio
async def test_reset_queue_stats_propagates_when_pipeline_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reset_queue_stats must raise on pipeline.execute failure."""
    from chaoscypher_core.services.reset import ResetOperations

    failing_pipeline = MagicMock()
    failing_pipeline.unlink.return_value = None
    failing_pipeline.execute.side_effect = _BoomError("pipeline.execute failed")

    fake_client = MagicMock()

    async def _fake_keys(_pattern: str) -> list[str]:
        return ["queue:task:1"]

    fake_client.keys.side_effect = _fake_keys
    fake_client.pipeline.return_value = failing_pipeline

    fake_queue_client = MagicMock()
    fake_queue_client.client = fake_client
    fake_queue_client.monitor = None

    monkeypatch.setattr(
        "chaoscypher_core.queue.queue_client",
        fake_queue_client,
    )

    ops = ResetOperations(database_name="test_db", settings_manager=None)  # type: ignore[arg-type]
    with pytest.raises(_BoomError):
        await ops.reset_queue_stats()
