# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Task 4.2: recommit cleanup atomicity.

If search-side delete fails mid-cleanup, graph-side delete must roll back
so there are no orphan rows on either side.
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(graph_mock, search_mock, sources_mock, indexing_mock, adapter_mock):
    """Construct a SourceCommitService using __new__ to avoid full init."""
    from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService

    svc = SourceCommitService.__new__(SourceCommitService)
    svc.graph_repository = graph_mock
    svc.search_repository = search_mock
    svc.sources_repository = sources_mock
    svc.indexing_repository = indexing_mock
    svc.adapter = adapter_mock
    return svc


class _FakeAdapter:
    """Minimal adapter that exposes transaction() and records commit/rollback."""

    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.session = MagicMock()

    @contextlib.contextmanager
    def transaction(self):
        try:
            yield
            self.committed = True
        except Exception:
            self.rolled_back = True
            raise


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRecommitCleanupAtomicity:
    """_cleanup_previous_commit wraps graph + search deletes in one transaction."""

    def test_transaction_entered_during_cleanup(self):
        """adapter.transaction() context manager is used in _cleanup_previous_commit."""
        adapter = _FakeAdapter()
        graph = MagicMock()
        graph.delete_graph_data_by_source.return_value = {
            "nodes_deleted": 0,
            "edges_deleted": 0,
            "templates_deleted": 0,
            "deleted_node_ids": [],
        }
        sources = MagicMock()
        sources.delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        search = MagicMock()
        search.delete_nodes_batch.return_value = 0
        indexing = MagicMock()

        svc = _make_service(graph, search, sources, indexing, adapter)
        svc._cleanup_previous_commit("f1", "s1")

        assert adapter.committed, "transaction() did not commit on success"
        assert not adapter.rolled_back

    def test_search_failure_triggers_rollback(self):
        """If search delete raises, the transaction rolls back (no committed graph delete)."""
        adapter = _FakeAdapter()
        graph = MagicMock()
        graph.delete_graph_data_by_source.return_value = {
            "nodes_deleted": 2,
            "edges_deleted": 1,
            "templates_deleted": 0,
            "deleted_node_ids": ["n1", "n2"],
        }
        sources = MagicMock()
        sources.delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        search = MagicMock()
        search.delete_nodes_batch.side_effect = RuntimeError("search index unavailable")
        indexing = MagicMock()

        svc = _make_service(graph, search, sources, indexing, adapter)

        import pytest

        with pytest.raises(RuntimeError, match="search index unavailable"):
            svc._cleanup_previous_commit("f1", "s1")

        assert adapter.rolled_back, "transaction() did not roll back on search failure"
        assert not adapter.committed, "transaction() must not commit when search delete fails"

    def test_graph_failure_triggers_rollback(self):
        """If graph delete raises, the transaction rolls back before search is reached."""
        adapter = _FakeAdapter()
        graph = MagicMock()
        graph.delete_graph_data_by_source.side_effect = RuntimeError("graph unavailable")
        sources = MagicMock()
        search = MagicMock()
        indexing = MagicMock()

        svc = _make_service(graph, search, sources, indexing, adapter)

        import pytest

        with pytest.raises(RuntimeError, match="graph unavailable"):
            svc._cleanup_previous_commit("f1", "s1")

        assert adapter.rolled_back
        assert not adapter.committed
        # Search delete was never reached
        search.delete_nodes_batch.assert_not_called()

    def test_delete_nodes_batch_called_with_session(self):
        """delete_nodes_batch receives session= so it joins the adapter's transaction."""
        adapter = _FakeAdapter()
        graph = MagicMock()
        graph.delete_graph_data_by_source.return_value = {
            "nodes_deleted": 2,
            "edges_deleted": 0,
            "templates_deleted": 0,
            "deleted_node_ids": ["n1", "n2"],
        }
        sources = MagicMock()
        sources.delete_citations_by_source.return_value = {
            "entity_citations_deleted": 0,
            "relationship_citations_deleted": 0,
        }
        search = MagicMock()
        search.delete_nodes_batch.return_value = 2
        indexing = MagicMock()

        svc = _make_service(graph, search, sources, indexing, adapter)
        svc._cleanup_previous_commit("f1", "s1")

        search.delete_nodes_batch.assert_called_once_with(["n1", "n2"], session=adapter.session)

    def test_success_path_all_operations_called(self):
        """Happy path: graph, search, citations, and chunk-status all execute."""
        adapter = _FakeAdapter()
        graph = MagicMock()
        graph.delete_graph_data_by_source.return_value = {
            "nodes_deleted": 3,
            "edges_deleted": 2,
            "templates_deleted": 1,
            "deleted_node_ids": ["n1", "n2", "n3"],
        }
        sources = MagicMock()
        sources.delete_citations_by_source.return_value = {
            "entity_citations_deleted": 5,
            "relationship_citations_deleted": 2,
        }
        search = MagicMock()
        search.delete_nodes_batch.return_value = 3
        indexing = MagicMock()

        svc = _make_service(graph, search, sources, indexing, adapter)
        result = svc._cleanup_previous_commit("f1", "s1")

        graph.delete_graph_data_by_source.assert_called_once_with("s1")
        search.delete_nodes_batch.assert_called_once()
        sources.delete_citations_by_source.assert_called_once_with("s1")
        indexing.update_chunk_status.assert_called_once_with("f1", "indexed")
        assert result["had_previous_data"] is True
        assert result["search_removed"] == 3
