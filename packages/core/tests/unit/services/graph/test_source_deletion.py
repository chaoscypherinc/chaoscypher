# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SourceService.delete_source cascade orchestration."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.graph.management.source import SourceService
from chaoscypher_core.settings import BatchingSettings


def _make_default_repo() -> MagicMock:
    """Create a mock SourcesProtocol with sensible defaults for delete_source tests."""
    repo = MagicMock()
    repo.get_orphaned_entity_uris.return_value = []
    repo.delete_source_db.return_value = True
    repo.get_source.return_value = {"id": "src1", "filepath": None}

    @contextmanager
    def _transaction():
        yield

    repo.transaction.side_effect = _transaction
    return repo


@pytest.fixture
def mock_repo():
    """Create a mock SourcesProtocol."""
    return _make_default_repo()


@pytest.fixture
def mock_graph_repo():
    """Create a mock GraphRepository."""
    return MagicMock()


@pytest.fixture
def mock_search_repo():
    """Create a mock SearchRepository."""
    repo = MagicMock()
    repo.remove_embeddings_batch.return_value = 0
    return repo


@pytest.fixture
def service(mock_repo):
    """Create SourceService with mock repository."""
    return SourceService(repository=mock_repo, database_name="test_db")


# ============================================================================
# Orchestration
# ============================================================================


class TestDeleteSourceOrchestration:
    """Tests for the main delete_source orchestration flow."""

    def test_collects_orphans_before_deletion(self, service, mock_repo) -> None:
        mock_repo.get_orphaned_entity_uris.return_value = ["uri/node1"]
        service.delete_source("src1")
        # Orphan detection must happen before delete
        mock_repo.get_orphaned_entity_uris.assert_called_once_with("src1")

    def test_delegates_to_repository(self, service, mock_repo) -> None:
        result = service.delete_source("src1")
        assert result is True
        mock_repo.delete_source_db.assert_called_once_with("src1", database_name="test_db")

    def test_returns_false_when_not_found(self, service, mock_repo) -> None:
        mock_repo.delete_source_db.return_value = False
        result = service.delete_source("missing")
        assert result is False

    def test_returns_true_on_success(self, service, mock_repo) -> None:
        assert service.delete_source("src1") is True

    def test_collects_chunk_ids_when_search_repo_provided(
        self, service, mock_repo, mock_search_repo
    ) -> None:
        mock_repo.get_chunks_by_source.return_value = (
            [{"id": "c1"}, {"id": "c2"}],
            2,
        )
        service.delete_source("src1", search_repo=mock_search_repo)
        mock_repo.get_chunks_by_source.assert_called_once_with(
            "src1", page=1, page_size=BatchingSettings().chunk_fetch_limit
        )

    def test_skips_chunk_collection_without_search_repo(self, service, mock_repo) -> None:
        service.delete_source("src1")
        mock_repo.get_chunks_by_source.assert_not_called()


# ============================================================================
# Graph Cleanup
# ============================================================================


class TestGraphCleanup:
    """Tests for orphaned node cleanup from RDF graph."""

    def test_deletes_orphaned_nodes(self, service, mock_repo, mock_graph_repo) -> None:
        mock_repo.get_orphaned_entity_uris.return_value = [
            "http://example.org/node1",
            "http://example.org/node2",
        ]
        service.delete_source("src1", graph_repo=mock_graph_repo)
        mock_graph_repo.delete_nodes_batch.assert_called_once_with(node_ids=["node1", "node2"])

    def test_extracts_node_id_from_uri(self, service, mock_repo, mock_graph_repo) -> None:
        mock_repo.get_orphaned_entity_uris.return_value = ["ns/entity/abc123"]
        service.delete_source("src1", graph_repo=mock_graph_repo)
        mock_graph_repo.delete_nodes_batch.assert_called_once_with(node_ids=["abc123"])

    def test_handles_plain_id_without_slash(self, service, mock_repo, mock_graph_repo) -> None:
        mock_repo.get_orphaned_entity_uris.return_value = ["plain-id"]
        service.delete_source("src1", graph_repo=mock_graph_repo)
        mock_graph_repo.delete_nodes_batch.assert_called_once_with(node_ids=["plain-id"])

    def test_skips_graph_cleanup_without_repo(self, service, mock_repo) -> None:
        mock_repo.get_orphaned_entity_uris.return_value = ["uri/node1"]
        # No graph_repo → no exception
        service.delete_source("src1", graph_repo=None)

    def test_graph_failure_propagates_exception(self, service, mock_repo, mock_graph_repo) -> None:
        """Graph errors now propagate — they're inside the atomic transaction."""
        mock_repo.get_orphaned_entity_uris.return_value = ["uri/node1", "uri/node2"]

        @contextmanager
        def _raising_transaction():
            try:
                yield
            except Exception:
                raise

        mock_repo.transaction.side_effect = _raising_transaction
        mock_graph_repo.delete_nodes_batch.side_effect = Exception("fail")

        with pytest.raises(Exception, match="fail"):
            service.delete_source("src1", graph_repo=mock_graph_repo)


# ============================================================================
# Search Cleanup
# ============================================================================


class TestSearchCleanup:
    """Tests for search index cleanup."""

    def test_removes_orphaned_nodes_from_search(self, service, mock_repo, mock_search_repo) -> None:
        mock_repo.get_orphaned_entity_uris.return_value = ["uri/node1"]
        mock_repo.get_chunks_by_source.return_value = ([], 0)
        service.delete_source("src1", search_repo=mock_search_repo)
        mock_search_repo.delete_node.assert_called_once_with("node1")

    def test_removes_chunk_embeddings(self, service, mock_repo, mock_search_repo) -> None:
        mock_repo.get_chunks_by_source.return_value = (
            [{"id": "c1"}, {"id": "c2"}],
            2,
        )
        service.delete_source("src1", search_repo=mock_search_repo)
        mock_search_repo.remove_embeddings_batch.assert_called_once_with(
            ["chunk:c1", "chunk:c2"], "chunk"
        )

    def test_skips_search_cleanup_without_repo(self, service, mock_repo) -> None:
        mock_repo.get_orphaned_entity_uris.return_value = ["uri/node1"]
        # No search_repo → no exception
        service.delete_source("src1", search_repo=None)

    def test_survives_individual_search_failures(
        self, service, mock_repo, mock_search_repo
    ) -> None:
        mock_repo.get_orphaned_entity_uris.return_value = ["uri/n1", "uri/n2"]
        mock_repo.get_chunks_by_source.return_value = (
            [{"id": "c1"}],
            1,
        )
        mock_search_repo.delete_node.side_effect = [Exception("fail"), None]
        mock_search_repo.remove_embedding.side_effect = Exception("fail")
        # Should not raise — search is best-effort post-transaction
        result = service.delete_source("src1", search_repo=mock_search_repo)
        assert result is True


# ============================================================================
# Edge Cases
# ============================================================================


class TestDeleteSourceEdgeCases:
    """Tests for edge cases in delete_source."""

    def test_no_orphans(self, service, mock_repo, mock_graph_repo) -> None:
        result = service.delete_source("src1", graph_repo=mock_graph_repo)
        assert result is True
        mock_graph_repo.delete_nodes_batch.assert_not_called()

    def test_no_chunks_to_clean(self, service, mock_repo, mock_search_repo) -> None:
        mock_repo.get_chunks_by_source.return_value = ([], 0)
        result = service.delete_source("src1", search_repo=mock_search_repo)
        assert result is True
        mock_search_repo.remove_embedding.assert_not_called()

    def test_both_repos_none(self, service, mock_repo) -> None:
        mock_repo.get_orphaned_entity_uris.return_value = ["uri/n1"]
        result = service.delete_source("src1", graph_repo=None, search_repo=None)
        assert result is True
