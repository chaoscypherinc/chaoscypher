# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for transactional source deletion (Cluster F).

Verifies that SQL cascade + graph cleanup are atomic (both roll back on
failure), while search and file cleanup are best-effort post-transaction
(failures do not affect return value or SQL/graph state).
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from chaoscypher_core.services.graph.management.source import SourceService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(
    *,
    orphaned_uris: list[str] | None = None,
    chunks: list[dict] | None = None,
    delete_db_returns: bool = True,
    filepath: str | None = "/data/sources/src1/file.txt",
) -> MagicMock:
    """Build a mock repository with sensible defaults."""
    repo = MagicMock()
    repo.get_orphaned_entity_uris.return_value = orphaned_uris or []
    repo.get_chunks_by_source.return_value = (chunks or [], len(chunks or []))
    repo.delete_source_db.return_value = delete_db_returns
    repo.get_source.return_value = (
        {"id": "src1", "filepath": filepath} if filepath else {"id": "src1", "filepath": None}
    )

    # transaction() context manager — default: commits (no-op in mock)
    @contextmanager
    def _transaction():
        yield

    repo.transaction.side_effect = _transaction
    return repo


def _make_graph_repo() -> MagicMock:
    """Build a mock GraphRepository."""
    return MagicMock()


def _make_search_repo() -> MagicMock:
    """Build a mock SearchRepository."""
    repo = MagicMock()
    repo.remove_embeddings_batch.return_value = 0
    return repo


def _service(repo: MagicMock) -> SourceService:
    """Create SourceService with the given mock repo."""
    return SourceService(repository=repo, database_name="test_db")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestSuccessfulDelete:
    """Happy path: all layers cleaned up when no errors occur."""

    def test_returns_true_on_success(self) -> None:
        """delete_source returns True when source is found and deleted."""
        repo = _make_repo()
        service = _service(repo)
        result = service.delete_source("src1")
        assert result is True

    def test_transaction_wraps_sql_and_graph(self) -> None:
        """SQL delete_source_db is called inside transaction()."""
        repo = _make_repo(orphaned_uris=["ns/entity/node1"])
        graph_repo = _make_graph_repo()
        service = _service(repo)

        call_order: list[str] = []

        @contextmanager
        def _tracking_transaction():
            call_order.append("transaction_enter")
            yield
            call_order.append("transaction_exit")

        repo.transaction.side_effect = _tracking_transaction
        repo.delete_source_db.side_effect = lambda *a, **kw: (
            call_order.append("delete_source_db") or True
        )
        graph_repo.delete_nodes_batch.side_effect = lambda *a, **kw: call_order.append(
            "delete_nodes_batch"
        )

        service.delete_source("src1", graph_repo=graph_repo)

        assert call_order.index("transaction_enter") < call_order.index("delete_source_db")
        assert call_order.index("transaction_enter") < call_order.index("delete_nodes_batch")
        assert call_order.index("delete_source_db") < call_order.index("transaction_exit")
        assert call_order.index("delete_nodes_batch") < call_order.index("transaction_exit")

    def test_graph_nodes_deleted_for_orphaned_uris(self) -> None:
        """graph_repo.delete_nodes_batch is called with all orphaned node IDs."""
        repo = _make_repo(orphaned_uris=["http://example.org/node1", "http://example.org/node2"])
        graph_repo = _make_graph_repo()
        service = _service(repo)

        service.delete_source("src1", graph_repo=graph_repo)

        graph_repo.delete_nodes_batch.assert_called_once_with(node_ids=["node1", "node2"])

    def test_search_nodes_deleted_post_transaction(self) -> None:
        """search_repo.delete_node is called after transaction commits."""
        repo = _make_repo(orphaned_uris=["ns/entity/node1"])
        search_repo = _make_search_repo()
        service = _service(repo)

        call_order: list[str] = []

        @contextmanager
        def _tracking_transaction():
            yield
            call_order.append("transaction_committed")

        repo.transaction.side_effect = _tracking_transaction
        search_repo.delete_node.side_effect = lambda *a, **kw: call_order.append(
            "search_delete_node"
        )

        service.delete_source("src1", search_repo=search_repo)

        assert "transaction_committed" in call_order
        assert "search_delete_node" in call_order
        assert call_order.index("transaction_committed") < call_order.index("search_delete_node")

    def test_chunk_embeddings_cleaned_post_transaction(self) -> None:
        """Chunk embeddings are removed from search after the transaction."""
        repo = _make_repo(chunks=[{"id": "c1"}, {"id": "c2"}])
        search_repo = _make_search_repo()
        service = _service(repo)

        service.delete_source("src1", search_repo=search_repo)

        search_repo.remove_embeddings_batch.assert_called_once_with(
            ["chunk:c1", "chunk:c2"], "chunk"
        )

    def test_files_deleted_post_transaction(self) -> None:
        """delete_source_files is called after the transaction commits."""
        repo = _make_repo(filepath="/data/sources/src1/file.txt")
        service = _service(repo)

        call_order: list[str] = []

        @contextmanager
        def _tracking_transaction():
            yield
            call_order.append("transaction_committed")

        repo.transaction.side_effect = _tracking_transaction
        repo.delete_source_files.side_effect = lambda *a, **kw: call_order.append("delete_files")

        service.delete_source("src1")

        assert "transaction_committed" in call_order
        assert "delete_files" in call_order
        assert call_order.index("transaction_committed") < call_order.index("delete_files")

    def test_returns_false_when_source_not_found(self) -> None:
        """Returns False and does not raise when delete_source_db returns False."""
        repo = _make_repo(delete_db_returns=False)
        service = _service(repo)
        result = service.delete_source("missing")
        assert result is False


# ---------------------------------------------------------------------------
# Atomic rollback tests
# ---------------------------------------------------------------------------


class TestAtomicRollback:
    """SQL and graph deletes must both roll back on any failure."""

    def test_graph_delete_failure_propagates_exception(self) -> None:
        """If graph_repo.delete_nodes_batch raises, the exception propagates up."""
        repo = _make_repo(orphaned_uris=["ns/entity/node1"])
        graph_repo = _make_graph_repo()
        graph_repo.delete_nodes_batch.side_effect = RuntimeError("graph write failed")
        service = _service(repo)

        # transaction() on a real mock just yields — we need it to propagate
        @contextmanager
        def _propagating_transaction():
            yield  # exceptions propagate naturally to caller

        repo.transaction.side_effect = _propagating_transaction

        with pytest.raises(RuntimeError, match="graph write failed"):
            service.delete_source("src1", graph_repo=graph_repo)

    def test_graph_delete_failure_aborts_transaction_before_sql_commit(self) -> None:
        """If graph_repo.delete_nodes_batch raises before delete_source_db, transaction rolls back."""
        repo = _make_repo(orphaned_uris=["ns/entity/node1"])
        graph_repo = _make_graph_repo()
        graph_repo.delete_nodes_batch.side_effect = RuntimeError("graph write failed")

        rolled_back = [False]

        @contextmanager
        def _tracking_transaction():
            try:
                yield
            except Exception:
                rolled_back[0] = True
                raise

        repo.transaction.side_effect = _tracking_transaction

        service = _service(repo)
        with pytest.raises(RuntimeError):
            service.delete_source("src1", graph_repo=graph_repo)

        # Transaction was aborted — delete_source_db should NOT have been committed
        assert rolled_back[0] is True
        # delete_source_db may have been called (ordering: graph first), but since
        # the transaction rolled back, the SQL change is not persisted.
        # We can verify graph was attempted:
        graph_repo.delete_nodes_batch.assert_called_once_with(node_ids=["node1"])

    def test_sql_cascade_failure_propagates_exception(self) -> None:
        """If delete_source_db raises, the exception propagates up."""
        repo = _make_repo()
        repo.delete_source_db.side_effect = RuntimeError("sql cascade failed")

        rolled_back = [False]

        @contextmanager
        def _tracking_transaction():
            try:
                yield
            except Exception:
                rolled_back[0] = True
                raise

        repo.transaction.side_effect = _tracking_transaction

        service = _service(repo)
        with pytest.raises(RuntimeError, match="sql cascade failed"):
            service.delete_source("src1")

        assert rolled_back[0] is True

    def test_sql_failure_does_not_call_post_transaction_cleanup(self) -> None:
        """If the transaction raises, search + file cleanup are not called."""
        repo = _make_repo(
            orphaned_uris=["ns/entity/node1"],
            chunks=[{"id": "c1"}],
            filepath="/data/sources/src1/file.txt",
        )
        search_repo = _make_search_repo()
        repo.delete_source_db.side_effect = RuntimeError("sql cascade failed")

        @contextmanager
        def _propagating_transaction():
            yield

        repo.transaction.side_effect = _propagating_transaction

        service = _service(repo)
        with pytest.raises(RuntimeError):
            service.delete_source("src1", search_repo=search_repo)

        # Post-transaction steps must NOT run
        search_repo.delete_node.assert_not_called()
        search_repo.remove_embeddings_batch.assert_not_called()
        repo.delete_source_files.assert_not_called()


# ---------------------------------------------------------------------------
# Best-effort post-transaction failures
# ---------------------------------------------------------------------------


class TestBestEffortCleanup:
    """Search + file cleanup failures must not roll back or raise."""

    def test_search_node_delete_failure_returns_true(self, caplog) -> None:
        """Search cleanup failure is logged as warning; delete_source returns True."""
        import logging

        repo = _make_repo(orphaned_uris=["ns/entity/node1"])
        search_repo = _make_search_repo()
        search_repo.delete_node.side_effect = RuntimeError("search unavailable")
        service = _service(repo)

        with caplog.at_level(logging.WARNING):
            result = service.delete_source("src1", search_repo=search_repo)

        assert result is True

    def test_search_node_delete_failure_does_not_affect_sql(self) -> None:
        """SQL is already committed before search cleanup; failure is independent."""
        repo = _make_repo(orphaned_uris=["ns/entity/node1"])
        search_repo = _make_search_repo()
        search_repo.delete_node.side_effect = RuntimeError("search unavailable")
        service = _service(repo)

        result = service.delete_source("src1", search_repo=search_repo)

        # SQL delete was called and completed
        repo.delete_source_db.assert_called_once()
        assert result is True

    def test_chunk_embeddings_failure_returns_true(self, caplog) -> None:
        """Chunk embedding cleanup failure is warn-only."""
        import logging

        repo = _make_repo(chunks=[{"id": "c1"}])
        search_repo = _make_search_repo()
        search_repo.remove_embeddings_batch.side_effect = RuntimeError("vector store down")
        service = _service(repo)

        with caplog.at_level(logging.WARNING):
            result = service.delete_source("src1", search_repo=search_repo)

        assert result is True

    def test_file_cleanup_failure_returns_true(self, caplog) -> None:
        """File deletion failure is warn-only; source is still considered deleted."""
        import logging

        repo = _make_repo(filepath="/data/sources/src1/file.txt")
        repo.delete_source_files.side_effect = OSError("permission denied")
        service = _service(repo)

        with caplog.at_level(logging.WARNING):
            result = service.delete_source("src1")

        # Even if file cleanup raises, the function should not propagate it.
        # delete_source_files is called (best-effort), but failure is swallowed.
        # Since the mock raises, we verify the function still returns True
        # (the adapter's delete_source_files should handle this gracefully,
        # but the service itself should not re-raise).
        assert result is True

    def test_all_post_transaction_failures_return_true(self) -> None:
        """All post-transaction failures together still return True."""
        repo = _make_repo(
            orphaned_uris=["ns/entity/node1"],
            chunks=[{"id": "c1"}],
            filepath="/data/sources/src1/file.txt",
        )
        search_repo = _make_search_repo()
        search_repo.delete_node.side_effect = RuntimeError("fail")
        search_repo.remove_embeddings_batch.side_effect = RuntimeError("fail")
        repo.delete_source_files.side_effect = OSError("fail")
        service = _service(repo)

        result = service.delete_source("src1", search_repo=search_repo)
        assert result is True


# ---------------------------------------------------------------------------
# No-op / edge cases
# ---------------------------------------------------------------------------


class TestNoOpCases:
    """Edge cases: no graph repo, no search repo, no orphans."""

    def test_no_graph_repo_skips_graph_cleanup(self) -> None:
        """No graph_repo → transaction still runs, graph cleanup skipped."""
        repo = _make_repo(orphaned_uris=["ns/entity/node1"])
        service = _service(repo)

        result = service.delete_source("src1", graph_repo=None)
        assert result is True
        repo.transaction.assert_called_once()

    def test_no_search_repo_skips_search_cleanup(self) -> None:
        """No search_repo → chunk collection skipped, search cleanup skipped."""
        repo = _make_repo(chunks=[{"id": "c1"}])
        service = _service(repo)

        result = service.delete_source("src1", search_repo=None)
        assert result is True
        repo.get_chunks_by_source.assert_not_called()

    def test_no_orphans_skips_graph_and_search_node_deletes(self) -> None:
        """Zero orphaned URIs → delete_nodes_batch never called."""
        repo = _make_repo(orphaned_uris=[])
        graph_repo = _make_graph_repo()
        search_repo = _make_search_repo()
        service = _service(repo)

        service.delete_source("src1", graph_repo=graph_repo, search_repo=search_repo)

        graph_repo.delete_nodes_batch.assert_not_called()
        search_repo.delete_node.assert_not_called()

    def test_no_chunks_skips_embedding_cleanup(self) -> None:
        """Zero chunks → remove_embeddings_batch never called."""
        repo = _make_repo(chunks=[])
        search_repo = _make_search_repo()
        service = _service(repo)

        service.delete_source("src1", search_repo=search_repo)

        search_repo.remove_embeddings_batch.assert_not_called()

    def test_no_filepath_skips_file_cleanup(self) -> None:
        """Null filepath → delete_source_files not called."""
        repo = _make_repo(filepath=None)
        service = _service(repo)

        service.delete_source("src1")

        repo.delete_source_files.assert_not_called()

    def test_plain_id_uri_passed_to_delete_nodes_batch(self) -> None:
        """URIs without slash are passed as-is in the batch list."""
        repo = _make_repo(orphaned_uris=["plain-id"])
        graph_repo = _make_graph_repo()
        service = _service(repo)

        service.delete_source("src1", graph_repo=graph_repo)

        graph_repo.delete_nodes_batch.assert_called_once_with(node_ids=["plain-id"])


# ---------------------------------------------------------------------------
# Graph delete_nodes_batch with partial/zero matches (already absent)
# ---------------------------------------------------------------------------


class TestGraphDeleteNodeReturnsFalse:
    """delete_nodes_batch returning zero nodes_deleted must not trigger rollback."""

    def test_graph_delete_returning_false_does_not_rollback(self) -> None:
        """Nodes already absent from the graph must not trigger rollback.

        delete_nodes_batch silently skips absent nodes and returns a dict
        with nodes_deleted=0 when all nodes are absent. The source
        deletion must proceed successfully without rolling back.
        """
        repo = _make_repo(orphaned_uris=["ns/entity/node1", "ns/entity/node2"])
        graph_repo = _make_graph_repo()
        # All nodes absent — batch returns zero deletions (not an error)
        graph_repo.delete_nodes_batch.return_value = {
            "nodes_deleted": 0,
            "edges_deleted": 0,
            "not_found": ["node1", "node2"],
            "errors": [],
        }

        rolled_back = [False]

        @contextmanager
        def _tracking_transaction():
            try:
                yield
            except Exception:
                rolled_back[0] = True
                raise

        repo.transaction.side_effect = _tracking_transaction

        service = _service(repo)
        result = service.delete_source("src1", graph_repo=graph_repo)

        # Service completes successfully — zero deletions is not an error
        assert result is True
        assert rolled_back[0] is False
        # Batch was called with both node IDs
        graph_repo.delete_nodes_batch.assert_called_once_with(node_ids=["node1", "node2"])
        # SQL delete still happened
        repo.delete_source_db.assert_called_once()


# ---------------------------------------------------------------------------
# DB-lock retry tests
# ---------------------------------------------------------------------------


class TestDeleteSourceRetryOnLock:
    """delete_source retries the whole operation on SQLITE_BUSY."""

    def test_delete_retries_on_db_lock(self) -> None:
        """If adapter.transaction() raises SQLITE_BUSY once, retry succeeds.

        The retry wrapper re-runs the whole idempotent _delete_source_impl
        from scratch. Second attempt enters a real transaction and completes.
        """
        repo = _make_repo()

        call_count = 0

        @contextmanager
        def flaky_transaction():  # type: ignore[misc]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OperationalError("", {}, Exception("database is locked"))
            # Second+ call: behave like a normal commit
            yield

        repo.transaction.side_effect = flaky_transaction

        service = _service(repo)
        result = service.delete_source("src1")

        # First call raised lock error; second succeeded
        assert call_count == 2
        assert result is True

    def test_delete_exhausts_retries_and_raises(self) -> None:
        """After max_retries lock errors, OperationalError propagates."""
        repo = _make_repo()

        @contextmanager
        def always_locked():  # type: ignore[misc]
            raise OperationalError("", {}, Exception("database is locked"))
            yield  # type: ignore[misc]  # unreachable but required for @contextmanager

        repo.transaction.side_effect = always_locked

        service = _service(repo)
        # Default max_retries=5 — all fail
        with pytest.raises(OperationalError):
            service.delete_source("src1")


# ---------------------------------------------------------------------------
# Vision images cleanup (audit fix F32)
# ---------------------------------------------------------------------------


class TestVisionImagesCleanupOnDelete:
    """Source delete must remove the rendered vision PNG directory.

    The directory lives at ``{data_dir}/databases/<db>/images/<source_id>/``
    — outside the staged-file parent that ``delete_source_files`` removes.
    Without explicit cleanup it would orphan forever.
    """

    def _engine_settings_with_data_dir(self, tmp_path) -> MagicMock:
        """Build a stub EngineSettings exposing ``paths.data_dir``."""
        engine_settings = MagicMock()
        engine_settings.paths.data_dir = str(tmp_path)
        return engine_settings

    def test_vision_images_dir_removed_on_source_delete(self, tmp_path) -> None:
        """Pre-create the images dir → delete_source removes it."""
        from chaoscypher_core.operations.importing.indexing_handler import (
            vision_images_dir,
        )

        # Pre-create the rendered-images directory with two PNGs.
        images_dir = vision_images_dir(data_dir=tmp_path, database_name="test_db", source_id="src1")
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "page_1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (images_dir / "page_2.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        assert images_dir.exists()

        repo = _make_repo(filepath="/data/sources/src1/file.txt")
        service = SourceService(
            repository=repo,
            database_name="test_db",
            settings=self._engine_settings_with_data_dir(tmp_path),
        )

        result = service.delete_source("src1")

        assert result is True
        assert not images_dir.exists(), (
            f"vision images directory not removed on source delete: {images_dir}"
        )

    def test_no_images_dir_is_silent(self, tmp_path) -> None:
        """Source with no rendered images → delete still returns True (no-op cleanup)."""
        repo = _make_repo(filepath="/data/sources/src1/file.txt")
        service = SourceService(
            repository=repo,
            database_name="test_db",
            settings=self._engine_settings_with_data_dir(tmp_path),
        )

        # Sanity: directory really doesn't exist.
        from chaoscypher_core.operations.importing.indexing_handler import (
            vision_images_dir,
        )

        assert not vision_images_dir(
            data_dir=tmp_path, database_name="test_db", source_id="src1"
        ).exists()

        result = service.delete_source("src1")
        assert result is True

    def test_vision_cleanup_failure_does_not_break_delete(self, tmp_path, monkeypatch) -> None:
        """Vision cleanup raising must not affect the delete return value."""
        from chaoscypher_core.operations.importing import indexing_handler

        repo = _make_repo(filepath="/data/sources/src1/file.txt")
        service = SourceService(
            repository=repo,
            database_name="test_db",
            settings=self._engine_settings_with_data_dir(tmp_path),
        )

        # Force the cleanup helper to raise something OTHER than OSError —
        # the surrounding try/except in _delete_source_impl must still
        # swallow it and return True.
        def _boom(**kwargs) -> None:
            raise RuntimeError("simulated cleanup explosion")

        monkeypatch.setattr(indexing_handler, "cleanup_vision_images", _boom)

        result = service.delete_source("src1")
        assert result is True
