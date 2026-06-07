# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral coverage tests for ``services/reset/operations.py``.

Covers the thin ``ResetOperations`` delegators (which forward 1:1 to their
sub-services), ``seed_templates`` (success + propagation), the
module-level ``_delete_import_files`` / ``_reset_search_indices`` helpers,
and the ``reset_queue_stats`` Redis-pipeline path (client present, client
None, monitor None).

The four sub-services are replaced with Mocks at construction time so the
delegators can be asserted in isolation. Lazily-imported factories are
patched at their *source* modules (the functions are imported inside the
methods under test, so patching the source name is what takes effect).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.services.reset import operations as ops_mod
from chaoscypher_core.services.reset.operations import ResetOperations, _delete_import_files


# ---------------------------------------------------------------------------
# Helpers (copied locally — no sibling test imports allowed)
# ---------------------------------------------------------------------------


def make_reset_ops() -> ResetOperations:
    """Construct ResetOperations with all four sub-services mocked.

    Patches the sub-service classes at the operations-module import site so
    no real services are built, then returns the wired instance with
    MagicMock sub-services attached.
    """
    with (
        patch.object(ops_mod, "WorkflowSystemResetService", MagicMock()),
        patch.object(ops_mod, "DataResetService", MagicMock()),
        patch.object(ops_mod, "DatabaseResetService", MagicMock()),
        patch.object(ops_mod, "GraphCleanupService", MagicMock()),
    ):
        settings_manager = MagicMock(name="ConfigManager")
        instance = ResetOperations("testdb", settings_manager)

    # Replace the (already-mock) sub-services with fresh, explicit mocks.
    instance.workflow_system_reset = MagicMock(name="workflow_system_reset")
    instance.data_reset = MagicMock(name="data_reset")
    instance.database_reset = MagicMock(name="database_reset")
    instance.graph_cleanup = MagicMock(name="graph_cleanup")
    return instance


# ---------------------------------------------------------------------------
# Thin delegators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDelegators:
    def test_reset_workflow_system_delegates(self) -> None:
        ops = make_reset_ops()
        sentinel = {"workflow": "reset"}
        ops.workflow_system_reset.reset_all_components.return_value = sentinel

        result = ops.reset_workflow_system()

        assert result is sentinel
        ops.workflow_system_reset.reset_all_components.assert_called_once_with()

    def test_reset_source_processing_history_delegates(self) -> None:
        ops = make_reset_ops()
        sentinel = {"history": "reset"}
        ops.data_reset.reset_source_processing_history.return_value = sentinel

        result = ops.reset_source_processing_history()

        assert result is sentinel
        ops.data_reset.reset_source_processing_history.assert_called_once_with()

    def test_reset_chats_delegates(self) -> None:
        ops = make_reset_ops()
        sentinel = {"chats": "reset"}
        ops.data_reset.reset_chats.return_value = sentinel

        result = ops.reset_chats()

        assert result is sentinel
        ops.data_reset.reset_chats.assert_called_once_with()

    def test_cleanup_orphaned_graph_items_delegates(self) -> None:
        ops = make_reset_ops()
        sentinel = {"orphans": 3}
        ops.graph_cleanup.cleanup_orphaned_items.return_value = sentinel

        result = ops.cleanup_orphaned_graph_items()

        assert result is sentinel
        ops.graph_cleanup.cleanup_orphaned_items.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_reset_all_delegates(self) -> None:
        ops = make_reset_ops()
        sentinel = {"nuked": True}
        ops.database_reset.reset_all = AsyncMock(return_value=sentinel)

        result = await ops.reset_all()

        assert result is sentinel
        ops.database_reset.reset_all.assert_awaited_once_with()


# ---------------------------------------------------------------------------
# seed_templates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSeedTemplates:
    def test_seed_templates_success(self) -> None:
        ops = make_reset_ops()
        ops.settings_manager.get_settings.return_value = MagicMock(current_database="testdb")

        with patch("chaoscypher_core.database.seed.seed_default_templates") as mock_seed:
            result = ops.seed_templates()

        mock_seed.assert_called_once_with("testdb")
        assert result["status"] == "success"
        assert "seeded" in result["message"].lower()

    def test_seed_templates_propagates_exception(self) -> None:
        ops = make_reset_ops()
        ops.settings_manager.get_settings.return_value = MagicMock(current_database="testdb")

        with patch(
            "chaoscypher_core.database.seed.seed_default_templates",
            side_effect=RuntimeError("seed boom"),
        ):
            with pytest.raises(RuntimeError, match="seed boom"):
                ops.seed_templates()


# ---------------------------------------------------------------------------
# _delete_import_files
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteImportFiles:
    @pytest.mark.asyncio
    async def test_deletes_existing_imports_dir(self, tmp_path) -> None:
        imports_dir = tmp_path / "imports"
        imports_dir.mkdir()
        (imports_dir / "file.txt").write_text("payload")

        settings = MagicMock()
        settings.database_dir = tmp_path
        settings.paths.imports_subdir = "imports"

        stats: dict[str, object] = {}
        # Real rmtree runs via the default executor — keep it real but on tmp.
        await _delete_import_files(settings, stats)

        assert stats["imports_directory_deleted"] is True
        assert not imports_dir.exists()

    @pytest.mark.asyncio
    async def test_absent_imports_dir_records_false(self, tmp_path) -> None:
        settings = MagicMock()
        settings.database_dir = tmp_path
        settings.paths.imports_subdir = "imports"  # never created

        stats: dict[str, object] = {}
        await _delete_import_files(settings, stats)

        assert stats["imports_directory_deleted"] is False

    @pytest.mark.asyncio
    async def test_rmtree_failure_propagates(self, tmp_path) -> None:
        imports_dir = tmp_path / "imports"
        imports_dir.mkdir()

        settings = MagicMock()
        settings.database_dir = tmp_path
        settings.paths.imports_subdir = "imports"

        stats: dict[str, object] = {}
        # Patch shutil.rmtree at source so run_in_executor surfaces the error.
        with patch("shutil.rmtree", side_effect=OSError("permission denied")):
            with pytest.raises(OSError, match="permission denied"):
                await _delete_import_files(settings, stats)


# ---------------------------------------------------------------------------
# _reset_search_indices
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResetSearchIndices:
    def test_clears_indices_and_invalidates_cache(self) -> None:
        mock_repo = MagicMock(name="search_repo")

        with (
            patch(
                "chaoscypher_core.repo_factories.get_search_repository",
                return_value=mock_repo,
            ) as mock_get,
            patch(
                "chaoscypher_core.repo_factories.search_factory.invalidate_search_repository"
            ) as mock_invalidate,
        ):
            stats: dict[str, object] = {}
            ops_mod._reset_search_indices("testdb", stats)

        mock_get.assert_called_once_with(database_name="testdb")
        mock_repo.clear_all_indices.assert_called_once_with()
        mock_invalidate.assert_called_once_with()
        assert stats["search_indices_cleared"] is True

    def test_failure_propagates(self) -> None:
        mock_repo = MagicMock(name="search_repo")
        mock_repo.clear_all_indices.side_effect = RuntimeError("index boom")

        with (
            patch(
                "chaoscypher_core.repo_factories.get_search_repository",
                return_value=mock_repo,
            ),
            patch("chaoscypher_core.repo_factories.search_factory.invalidate_search_repository"),
        ):
            with pytest.raises(RuntimeError, match="index boom"):
                ops_mod._reset_search_indices("testdb", {})


# ---------------------------------------------------------------------------
# reset_queue_stats
# ---------------------------------------------------------------------------


def _make_queue_client(
    *,
    task_keys: list[str],
    result_keys: list[str],
    pending_keys: list[str],
    running_keys: list[str],
    health_keys: list[str],
) -> tuple[AsyncMock, MagicMock]:
    """Build a fake Redis-backed queue client + pipeline.

    ``.keys()`` returns the configured key lists in call order matching the
    source: task, result, pending, running, health.
    """
    client = AsyncMock(name="redis-client")
    client.keys.side_effect = [
        task_keys,
        result_keys,
        pending_keys,
        running_keys,
        health_keys,
    ]
    pipeline = MagicMock(name="pipeline")
    pipeline.unlink = MagicMock()
    pipeline.execute = AsyncMock()
    client.pipeline = MagicMock(return_value=pipeline)
    return client, pipeline


@pytest.mark.unit
class TestResetQueueStats:
    @pytest.mark.asyncio
    async def test_success_deletes_keys_and_clears_stats(self) -> None:
        ops = make_reset_ops()
        client, pipeline = _make_queue_client(
            task_keys=["queue:task:1", "queue:task:2"],
            result_keys=["queue:result:1"],
            pending_keys=["queue:default:pending"],
            running_keys=["queue:default:running"],
            health_keys=["queue:default:health"],
        )
        monitor = AsyncMock(name="monitor")

        from chaoscypher_core.queue import queue_client

        with (
            patch.object(queue_client, "client", client),
            patch.object(queue_client, "monitor", monitor),
        ):
            result = await ops.reset_queue_stats()

        assert result["status"] == "success"
        assert result["tasks_deleted"] == 2
        assert result["results_deleted"] == 1
        # pending + running + health = 3
        assert result["queues_cleared"] == 3
        # 2 + 1 + 1 + 1 + 1 = 6 unlink calls
        assert pipeline.unlink.call_count == 6
        pipeline.execute.assert_awaited_once()
        monitor.clear_all_stats.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_no_keys_skips_pipeline_execute(self) -> None:
        ops = make_reset_ops()
        client, pipeline = _make_queue_client(
            task_keys=[],
            result_keys=[],
            pending_keys=[],
            running_keys=[],
            health_keys=[],
        )
        monitor = AsyncMock(name="monitor")

        from chaoscypher_core.queue import queue_client

        with (
            patch.object(queue_client, "client", client),
            patch.object(queue_client, "monitor", monitor),
        ):
            result = await ops.reset_queue_stats()

        assert result["status"] == "success"
        assert result["tasks_deleted"] == 0
        # all_keys is empty → execute never awaited
        pipeline.execute.assert_not_awaited()
        # monitor still cleared (separate guard)
        monitor.clear_all_stats.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_client_none_returns_error_early(self) -> None:
        ops = make_reset_ops()

        from chaoscypher_core.queue import queue_client

        with patch.object(queue_client, "client", None):
            result = await ops.reset_queue_stats()

        assert result["status"] == "error"
        assert "unavailable" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_monitor_none_logs_warning_but_succeeds(self) -> None:
        ops = make_reset_ops()
        client, pipeline = _make_queue_client(
            task_keys=["queue:task:1"],
            result_keys=[],
            pending_keys=[],
            running_keys=[],
            health_keys=[],
        )

        from chaoscypher_core.queue import queue_client

        with (
            patch.object(queue_client, "client", client),
            patch.object(queue_client, "monitor", None),
        ):
            result = await ops.reset_queue_stats()

        assert result["status"] == "success"
        assert result["tasks_deleted"] == 1
        pipeline.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_keys_failure_propagates(self) -> None:
        ops = make_reset_ops()
        client = AsyncMock(name="redis-client")
        client.keys.side_effect = RuntimeError("redis down")

        from chaoscypher_core.queue import queue_client

        with patch.object(queue_client, "client", client):
            with pytest.raises(RuntimeError, match="redis down"):
                await ops.reset_queue_stats()
