# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SourceCommitService._cleanup_previous_commit."""

from unittest.mock import MagicMock

import pytest

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
def service(mocks):
    """Create SourceCommitService with mocked dependencies."""
    return SourceCommitService(**mocks)


class TestCleanupPreviousCommit:
    """Tests for _cleanup_previous_commit."""

    def test_deletes_graph_data_first(self, service, mocks) -> None:
        mocks["graph_repository"].delete_graph_data_by_source.return_value = {
            "nodes_deleted": 0,
            "edges_deleted": 0,
            "templates_deleted": 0,
            "deleted_node_ids": [],
        }
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        service._cleanup_previous_commit("f1", "s1")
        mocks["graph_repository"].delete_graph_data_by_source.assert_called_once_with("s1")

    def test_cleans_search_for_deleted_nodes(self, service, mocks) -> None:
        mocks["graph_repository"].delete_graph_data_by_source.return_value = {
            "nodes_deleted": 2,
            "deleted_node_ids": ["n1", "n2"],
        }
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
        mocks["graph_repository"].delete_graph_data_by_source.return_value = {
            "nodes_deleted": 0,
            "deleted_node_ids": [],
        }
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        service._cleanup_previous_commit("f1", "s1")
        mocks["search_repository"].delete_nodes_batch.assert_not_called()

    def test_deletes_citations(self, service, mocks) -> None:
        mocks["graph_repository"].delete_graph_data_by_source.return_value = {
            "nodes_deleted": 0,
            "deleted_node_ids": [],
        }
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 5,
            "relationship_citations_deleted": 3,
        }
        service._cleanup_previous_commit("f1", "s1")
        mocks["sources_repository"].delete_citations_by_source.assert_called_once_with("s1")

    def test_resets_chunk_status(self, service, mocks) -> None:
        mocks["graph_repository"].delete_graph_data_by_source.return_value = {
            "nodes_deleted": 0,
            "deleted_node_ids": [],
        }
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        service._cleanup_previous_commit("f1", "s1")
        mocks["indexing_repository"].update_chunk_status.assert_called_once_with("f1", "indexed")

    def test_had_previous_data_true_when_nodes_deleted(self, service, mocks) -> None:
        mocks["graph_repository"].delete_graph_data_by_source.return_value = {
            "nodes_deleted": 3,
            "deleted_node_ids": ["n1", "n2", "n3"],
        }
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        mocks["search_repository"].delete_nodes_batch.return_value = 3
        result = service._cleanup_previous_commit("f1", "s1")
        assert result["had_previous_data"] is True

    def test_had_previous_data_true_when_citations_deleted(self, service, mocks) -> None:
        mocks["graph_repository"].delete_graph_data_by_source.return_value = {
            "nodes_deleted": 0,
            "deleted_node_ids": [],
        }
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 5,
            "relationship_citations_deleted": 0,
        }
        result = service._cleanup_previous_commit("f1", "s1")
        assert result["had_previous_data"] is True

    def test_had_previous_data_false_when_nothing_deleted(self, service, mocks) -> None:
        mocks["graph_repository"].delete_graph_data_by_source.return_value = {
            "nodes_deleted": 0,
            "deleted_node_ids": [],
        }
        mocks["sources_repository"].delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        result = service._cleanup_previous_commit("f1", "s1")
        assert result["had_previous_data"] is False

    def test_returns_full_stats(self, service, mocks) -> None:
        graph_stats = {
            "nodes_deleted": 2,
            "edges_deleted": 1,
            "templates_deleted": 1,
            "deleted_node_ids": ["n1", "n2"],
        }
        citation_stats = {"entity_citations_deleted": 3, "relationship_citations_deleted": 2}
        mocks["graph_repository"].delete_graph_data_by_source.return_value = graph_stats
        mocks["sources_repository"].delete_citations_by_source.return_value = citation_stats
        mocks["search_repository"].delete_nodes_batch.return_value = 2
        result = service._cleanup_previous_commit("f1", "s1")
        assert result["graph"] == graph_stats
        assert result["citations"] == citation_stats
        assert result["search_removed"] == 2
