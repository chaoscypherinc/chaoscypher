# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Both bulk resets release every source from the graph before deleting the source rows (#795).

``graph_*.source_id`` is ``ON DELETE SET NULL``: deleting the source rows takes
no graph rows with them. Without the release, every source-attested node and
edge would survive the reset with no home and no member.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.reset.data_reset import DataResetService
from chaoscypher_core.services.reset.operations import _delete_source_data


def _adapter() -> MagicMock:
    adapter = MagicMock(name="adapter")
    for count in (
        "count_sources",
        "count_staged_chunks",
        "count_embeddings",
        "count_extraction_tasks",
        "count_extraction_jobs",
        "count_chunks",
    ):
        getattr(adapter, count).return_value = 0
    return adapter


def _calls(adapter: MagicMock) -> list[str]:
    """The adapter's and its graph repository's calls, in order, by name."""
    return [name for name, _args, _kwargs in adapter.mock_calls]


@pytest.fixture
def adapter() -> MagicMock:
    adapter = _adapter()
    with (
        patch("chaoscypher_core.database.adapter_factory.get_sqlite_adapter", return_value=adapter),
        patch(
            "chaoscypher_core.services.reset.data_reset.get_sqlite_adapter", return_value=adapter
        ),
        patch(
            "chaoscypher_core.repo_factories.get_transaction_graph_repository",
            side_effect=lambda a, _db: a.graph,
        ),
        patch(
            "chaoscypher_core.services.reset.data_reset.get_transaction_graph_repository",
            side_effect=lambda a, _db: a.graph,
        ),
    ):
        yield adapter


def _assert_released_before_deleted(adapter: MagicMock) -> None:
    calls = _calls(adapter)
    assert "graph.release_all_sources" in calls
    assert calls.index("graph.release_all_sources") < calls.index("delete_all_sources")
    # inside the one transaction the source rows are deleted in
    assert calls.index("transaction().__enter__") < calls.index("graph.release_all_sources")
    assert calls.index("delete_all_sources") < calls.index("transaction().__exit__")


def test_knowledge_reset_releases_sources_before_deleting_them(adapter: MagicMock) -> None:
    _delete_source_data("db", {})
    _assert_released_before_deleted(adapter)


def test_processing_history_reset_releases_sources_before_deleting_them(
    adapter: MagicMock,
) -> None:
    with patch("chaoscypher_core.services.reset.data_reset.get_settings"):
        DataResetService("db").reset_source_processing_history()
    _assert_released_before_deleted(adapter)
