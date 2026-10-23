# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SourceService.delete_source cascade orchestration."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.models import ReleasedRows
from chaoscypher_core.services.graph.management.source import SourceService
from chaoscypher_core.settings import BatchingSettings


def _make_default_repo() -> MagicMock:
    """Create a mock SourcesProtocol with sensible defaults for delete_source tests."""
    repo = MagicMock()
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
def mock_graph_repo(monkeypatch, mock_repo):
    """The graph repository the delete builds on the adapter's session (released nothing)."""
    graph = MagicMock()
    graph.release_source.return_value = ReleasedRows()
    built: list[tuple[object, str]] = []

    def _build(adapter, database_name):
        built.append((adapter, database_name))
        return graph

    monkeypatch.setattr("chaoscypher_core.repo_factories.get_transaction_graph_repository", _build)
    graph.built = built
    return graph


@pytest.fixture
def mock_search_repo():
    """Create a mock SearchRepository."""
    repo = MagicMock()
    repo.remove_embeddings_batch.return_value = 0
    return repo


@pytest.fixture
def service(mock_repo, mock_graph_repo):
    """Create SourceService with mock repository."""
    return SourceService(repository=mock_repo, database_name="test_db")


# ============================================================================
# Orchestration
# ============================================================================


class TestDeleteSourceOrchestration:
    """Tests for the main delete_source orchestration flow."""

    def test_releases_the_graph_before_the_source_row(
        self, service, mock_repo, mock_graph_repo
    ) -> None:
        order: list[str] = []
        mock_graph_repo.release_source.side_effect = lambda sid: (
            order.append("release") or ReleasedRows()
        )
        mock_repo.delete_source_db.side_effect = lambda *a, **kw: order.append("delete") or True
        service.delete_source("src1")
        # graph_*.source_id is ON DELETE SET NULL: the row delete takes no graph
        # rows, so the release must have run first, in the same transaction.
        assert order == ["release", "delete"]
        mock_graph_repo.release_source.assert_called_once_with("src1")

    def test_release_runs_on_the_adapters_own_session(
        self, service, mock_repo, mock_graph_repo
    ) -> None:
        service.delete_source("src1")
        assert mock_graph_repo.built == [(mock_repo, "test_db")]

    def test_delegates_to_repository(self, service, mock_repo) -> None:
        result = service.delete_source("src1")
        assert result.deleted is True
        mock_repo.delete_source_db.assert_called_once_with("src1", database_name="test_db")

    def test_returns_false_when_not_found(self, service, mock_repo) -> None:
        mock_repo.delete_source_db.return_value = False
        result = service.delete_source("missing")
        assert result.deleted is False

    def test_an_unknown_source_releases_nothing(self, service, mock_repo, mock_graph_repo) -> None:
        """Another database's source (or none) is never released: get_source is scoped."""
        mock_repo.get_source.return_value = None
        assert service.delete_source("elsewhere").deleted is False
        mock_repo.get_source.assert_called_once_with("elsewhere", "test_db")
        mock_graph_repo.release_source.assert_not_called()
        mock_repo.delete_source_db.assert_not_called()

    def test_returns_true_on_success(self, service, mock_repo) -> None:
        assert service.delete_source("src1").deleted is True

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
# Graph Release
# ============================================================================


class TestGraphRelease:
    """The graph side of a delete is one release of the source (#795, D5)."""

    def test_graph_failure_propagates_exception(self, service, mock_repo, mock_graph_repo) -> None:
        """Graph errors propagate — they're inside the atomic transaction."""
        rolled_back = [False]

        @contextmanager
        def _tracking_transaction():
            try:
                yield
            except Exception:
                rolled_back[0] = True
                raise

        mock_repo.transaction.side_effect = _tracking_transaction
        mock_graph_repo.release_source.side_effect = Exception("fail")

        with pytest.raises(Exception, match="fail"):
            service.delete_source("src1")
        assert rolled_back[0] is True
        mock_repo.delete_source_db.assert_not_called()


# ============================================================================
# Search Cleanup
# ============================================================================


class TestSearchCleanup:
    """Tests for search index cleanup."""

    def test_removes_released_nodes_from_search(
        self, service, mock_repo, mock_graph_repo, mock_search_repo
    ) -> None:
        mock_graph_repo.release_source.return_value = ReleasedRows(
            deleted_node_ids=frozenset({"node2", "node1"}), touched_node_ids=frozenset({"kept"})
        )
        mock_repo.get_chunks_by_source.return_value = ([], 0)
        result = service.delete_source("src1", search_repo=mock_search_repo)
        # the caller's post-release refresh removes them, from the release it gets back
        mock_search_repo.delete_nodes_batch.assert_not_called()
        assert result.released.deleted_node_ids == {"node1", "node2"}

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
        # No search_repo → no exception
        service.delete_source("src1", search_repo=None)

    def test_survives_individual_search_failures(
        self, service, mock_repo, mock_graph_repo, mock_search_repo
    ) -> None:
        mock_graph_repo.release_source.return_value = ReleasedRows(
            deleted_node_ids=frozenset({"n1", "n2"})
        )
        mock_repo.get_chunks_by_source.return_value = (
            [{"id": "c1"}],
            1,
        )
        mock_search_repo.delete_nodes_batch.side_effect = Exception("fail")
        mock_search_repo.remove_embedding.side_effect = Exception("fail")
        # Should not raise — search is best-effort post-transaction
        result = service.delete_source("src1", search_repo=mock_search_repo)
        assert result.deleted is True


# ============================================================================
# Edge Cases
# ============================================================================


class TestDeleteSourceEdgeCases:
    """Tests for edge cases in delete_source."""

    def test_nothing_released_deletes_nothing_from_search(
        self, service, mock_repo, mock_search_repo
    ) -> None:
        mock_repo.get_chunks_by_source.return_value = ([], 0)
        result = service.delete_source("src1", search_repo=mock_search_repo)
        assert result.deleted is True
        mock_search_repo.delete_nodes_batch.assert_not_called()

    def test_no_chunks_to_clean(self, service, mock_repo, mock_search_repo) -> None:
        mock_repo.get_chunks_by_source.return_value = ([], 0)
        result = service.delete_source("src1", search_repo=mock_search_repo)
        assert result.deleted is True
        mock_search_repo.remove_embedding.assert_not_called()

    def test_no_search_repo(self, service, mock_repo, mock_graph_repo) -> None:
        result = service.delete_source("src1", search_repo=None)
        assert result.deleted is True
        mock_graph_repo.release_source.assert_called_once_with("src1")
