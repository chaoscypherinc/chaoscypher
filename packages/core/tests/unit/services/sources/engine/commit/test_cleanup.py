# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SourceCommitService._cleanup_previous_commit."""

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.models import ReleasedRows
from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService


@pytest.fixture
def mocks():
    """Create all mocked dependencies for SourceCommitService."""
    return {
        "graph_repository": MagicMock(),
        "source_repository": MagicMock(),
        "sources_repository": MagicMock(),
        "indexing_repository": MagicMock(),
        "search_repository": MagicMock(),
        "settings": MagicMock(),
    }


@pytest.fixture
def service(mocks, monkeypatch):
    """Create SourceCommitService with mocked dependencies.

    The release runs on a repository built from the adapter (``source_repository``);
    it is handed ``mocks["graph_repository"]`` so the tests below can drive it.
    """
    built: list[tuple[object, str]] = []

    def _build(adapter, database_name):
        built.append((adapter, database_name))
        return mocks["graph_repository"]

    monkeypatch.setattr("chaoscypher_core.repo_factories.get_transaction_graph_repository", _build)
    svc = SourceCommitService(**mocks)
    svc.built_release_repositories = built
    return svc


class TestCleanupPreviousCommit:
    """Tests for _cleanup_previous_commit."""

    def test_releases_on_the_adapters_own_session(self, service, mocks) -> None:
        """Whatever graph repository the service holds, the release joins the adapter's transaction."""
        mocks["graph_repository"].release_source.return_value = ReleasedRows()
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        service._cleanup_previous_commit("f1", "s1")
        assert service.built_release_repositories == [
            (mocks["source_repository"], service.database_name)
        ]

    def test_releases_the_source_first(self, service, mocks) -> None:
        mocks["graph_repository"].release_source.return_value = ReleasedRows()
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        service._cleanup_previous_commit("f1", "s1")
        mocks["graph_repository"].release_source.assert_called_once_with("s1")

    def test_cleans_search_for_deleted_nodes(self, service, mocks) -> None:
        mocks["graph_repository"].release_source.return_value = ReleasedRows(
            deleted_node_ids=frozenset(["n1", "n2"])
        )
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        mocks["search_repository"].delete_nodes_batch.return_value = 2
        service._cleanup_previous_commit("f1", "s1")
        mocks["search_repository"].delete_nodes_batch.assert_called_once_with(
            ["n1", "n2"], session=mocks["source_repository"].session
        )

    def test_skips_search_when_no_deleted_nodes(self, service, mocks) -> None:
        mocks["graph_repository"].release_source.return_value = ReleasedRows()
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        service._cleanup_previous_commit("f1", "s1")
        mocks["search_repository"].delete_nodes_batch.assert_not_called()

    def test_deletes_citations(self, service, mocks) -> None:
        mocks["graph_repository"].release_source.return_value = ReleasedRows()
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 5,
            "relationship_citations_deleted": 3,
        }
        service._cleanup_previous_commit("f1", "s1")
        mocks["sources_repository"].delete_citations_by_source.assert_called_once_with("s1")

    def test_resets_chunk_status(self, service, mocks) -> None:
        mocks["graph_repository"].release_source.return_value = ReleasedRows()
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        service._cleanup_previous_commit("f1", "s1")
        mocks["indexing_repository"].update_chunk_status.assert_called_once_with("f1", "indexed")

    def test_had_previous_data_true_when_nodes_deleted(self, service, mocks) -> None:
        mocks["graph_repository"].release_source.return_value = ReleasedRows(
            deleted_node_ids=frozenset(["n1", "n2", "n3"])
        )
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        mocks["search_repository"].delete_nodes_batch.return_value = 3
        result = service._cleanup_previous_commit("f1", "s1")
        assert result["had_previous_data"] is True

    def test_had_previous_data_true_when_citations_deleted(self, service, mocks) -> None:
        mocks["graph_repository"].release_source.return_value = ReleasedRows()
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 5,
            "relationship_citations_deleted": 0,
        }
        result = service._cleanup_previous_commit("f1", "s1")
        assert result["had_previous_data"] is True

    def test_a_node_another_source_keeps_is_previous_data_and_stays_searchable(
        self, service, mocks
    ) -> None:
        """A re-commit after a merge: the shared node survives the release (#795)."""
        mocks["graph_repository"].release_source.return_value = ReleasedRows(
            touched_node_ids=frozenset({"shared"})
        )
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        result = service._cleanup_previous_commit("f1", "s1")
        assert result["had_previous_data"] is True
        mocks["search_repository"].delete_nodes_batch.assert_not_called()

    def test_had_previous_data_false_when_nothing_deleted(self, service, mocks) -> None:
        mocks["graph_repository"].release_source.return_value = ReleasedRows()
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        result = service._cleanup_previous_commit("f1", "s1")
        assert result["had_previous_data"] is False

    def test_returns_full_stats(self, service, mocks) -> None:
        citation_stats = {"entity_citations_deleted": 3, "relationship_citations_deleted": 2}
        mocks["graph_repository"].release_source.return_value = ReleasedRows(
            deleted_node_ids=frozenset({"n2", "n1"}),
            deleted_edge_ids=frozenset({"e1"}),
            touched_node_ids=frozenset({"kept"}),
        )
        mocks["sources_repository"].delete_citations_by_source.return_value = citation_stats
        mocks["search_repository"].delete_nodes_batch.return_value = 2
        result = service._cleanup_previous_commit("f1", "s1")
        assert result["graph"] == {
            "nodes_deleted": 2,
            "edges_deleted": 1,
            "nodes_kept": 1,
            "edges_kept": 0,
            "nodes_rebuilt": 0,
            "deleted_node_ids": ["n1", "n2"],
        }
        assert result["citations"] == citation_stats
        assert result["search_removed"] == 2
