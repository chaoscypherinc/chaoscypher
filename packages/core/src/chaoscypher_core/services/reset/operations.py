# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Reset Operations.

Heavy database and knowledge reset operations. Moved down from
``chaoscypher_cortex.features.settings.reset_operations`` in PR2b
Task 22. Return types are plain ``dict[str, Any]`` so Core stays
HTTP-agnostic; Cortex wraps them in ``ResetResponse`` at the API
boundary.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.reset.data_reset import DataResetService
from chaoscypher_core.services.reset.database_reset import DatabaseResetService
from chaoscypher_core.services.reset.graph_cleanup import GraphCleanupService
from chaoscypher_core.services.reset.workflow_system_reset import (
    WorkflowSystemResetService,
)


if TYPE_CHECKING:
    from chaoscypher_core.app_config import ConfigManager, Settings

logger = structlog.get_logger(__name__)


def _delete_source_data(database_name: str, stats: dict[str, object]) -> None:
    """Delete all source-related data in correct FK order.

    Removes source tags, citations, chunks, extraction artifacts,
    and source rows within a single session transaction.

    Args:
        database_name: Target database name.
        stats: Mutable stats dict updated with ``sources_deleted``
            and ``chunks_deleted`` counts.

    """
    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter

    adapter = get_sqlite_adapter(database_name=database_name)
    try:
        with adapter.transaction():
            try:
                sources_count = adapter.count_sources(database_name=database_name)
                chunks_count = adapter.count_chunks()
                logger.info(
                    "counts_before_delete",
                    sources=sources_count,
                    chunks=chunks_count,
                )

                # Wholesale deletes in FK order (children first). These are
                # cross-database resets — callers expect everything under the
                # source umbrella to be gone.
                adapter.clear_all_tag_assignments()
                adapter.clear_all_citations()
                adapter.delete_all_relationship_citations()
                adapter.clear_all_chunks()
                adapter.clear_all_tags()
                adapter.clear_all_embeddings()
                adapter.clear_all_extraction_tasks()
                adapter.clear_all_extraction_jobs()
                adapter.delete_all_sources(database_name=database_name)

                stats["sources_deleted"] = sources_count
                stats["chunks_deleted"] = chunks_count
                logger.info("all_knowledge_data_deleted", stats=stats)

            except Exception:
                logger.exception("knowledge_data_delete_failed")
                raise

        logger.info("database_changes_committed")
    finally:
        adapter.disconnect()


def _reset_knowledge_graph(database_name: str, stats: dict[str, object]) -> None:
    """Clear the knowledge graph and re-seed default templates.

    Args:
        database_name: Target database name.
        stats: Mutable stats dict updated with ``nodes_deleted``,
            ``edges_deleted``, and ``templates_deleted`` counts.

    """
    try:
        from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
        from chaoscypher_core.database.seed import seed_default_templates
        from chaoscypher_core.repo_factories import get_graph_repository

        adapter = get_sqlite_adapter(database_name=database_name)
        try:
            with adapter.transaction():
                graph_session = adapter.session
                assert graph_session is not None
                graph_repo = get_graph_repository(graph_session, database_name)
                graph_result = graph_repo.clear_all()
                stats["nodes_deleted"] = graph_result.get("nodes_removed", 0)
                stats["edges_deleted"] = graph_result.get("edges_removed", 0)
                stats["templates_deleted"] = graph_result.get("templates_removed", 0)
        finally:
            adapter.disconnect()

        logger.info("reseeding_default_templates")
        seed_default_templates(database_name)
        logger.info("default_templates_reseeded")
    except Exception:
        logger.exception("knowledge_graph_reset_failed")
        raise


async def _delete_import_files(settings: Settings, stats: dict[str, object]) -> None:
    """Delete the uploaded import files directory.

    Args:
        settings: Application settings with ``database_dir`` and ``paths``
            attributes.
        stats: Mutable stats dict updated with ``imports_directory_deleted``.

    """
    import asyncio
    import shutil

    try:
        imports_dir = settings.database_dir / settings.paths.imports_subdir
        if imports_dir.exists():
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, shutil.rmtree, imports_dir)
            stats["imports_directory_deleted"] = True
            logger.info("imports_directory_deleted", imports_dir=str(imports_dir))
        else:
            stats["imports_directory_deleted"] = False
    except Exception:
        logger.exception("imports_directory_delete_failed")
        raise


def _reset_search_indices(database_name: str, stats: dict[str, object]) -> None:
    """Clear all search indices and invalidate the repository cache.

    Args:
        database_name: Target database name.
        stats: Mutable stats dict updated with ``search_indices_cleared``.

    """
    try:
        from chaoscypher_core.repo_factories import get_search_repository
        from chaoscypher_core.repo_factories.search_factory import (
            invalidate_search_repository,
        )

        search_repo = get_search_repository(database_name=database_name)
        search_repo.clear_all_indices()
        stats["search_indices_cleared"] = True

        invalidate_search_repository()
    except Exception:
        logger.exception("search_indices_reset_failed")
        raise


class ResetOperations:
    """Database and knowledge reset operations.

    Encapsulates all heavy reset methods previously in SettingsService.
    """

    def __init__(self, database_name: str, settings_manager: ConfigManager) -> None:
        """Initialize reset operations.

        Args:
            database_name: The database to operate on.
            settings_manager: Configuration manager for settings access.
        """
        self.database_name = database_name
        self.settings_manager = settings_manager
        self.workflow_system_reset = WorkflowSystemResetService(database_name)
        self.data_reset = DataResetService(database_name)
        self.database_reset = DatabaseResetService(database_name)
        self.graph_cleanup = GraphCleanupService(database_name)

    def reset_workflow_system(self) -> dict[str, Any]:
        """Reset workflow system (tools, workflows, triggers) to defaults.

        SRP: Delegates to WorkflowSystemResetService.
        """
        return self.workflow_system_reset.reset_all_components()

    def reset_source_processing_history(self) -> dict[str, Any]:
        """Reset source_processing history.

        SRP: Delegates to DataResetService.
        """
        return self.data_reset.reset_source_processing_history()

    def reset_chats(self) -> dict[str, Any]:
        """Reset all chats.

        SRP: Delegates to DataResetService.
        """
        return self.data_reset.reset_chats()

    def cleanup_orphaned_graph_items(self) -> dict[str, Any]:
        """Clean up orphaned items from the graph.

        Removes items with invalid references (edges pointing to non-existent
        nodes, nodes/templates with source_id pointing to non-existent sources).
        Items with source_id=NULL are preserved as they are intentionally unlinked.

        SRP: Delegates to GraphCleanupService.
        """
        return self.graph_cleanup.cleanup_orphaned_items()

    async def reset_queue_stats(self) -> dict[str, Any]:
        """Reset queue system.

        Clears:
        - All task records (queue:task:*)
        - All result records (queue:result:*)
        - All pending queue sorted sets (queue:*:pending)
        - Running sets (queue:*:running)
        - Health keys (queue:*:health)
        - Token counts, costs, and task history

        Note: This is a fast bulk operation that bypasses individual task cancellation.

        SRP: Delegates to queue client.
        """
        from chaoscypher_core.queue import queue_client

        stats = {
            "tasks_deleted": 0,
            "results_deleted": 0,
            "queues_cleared": 0,
        }

        # Use pipeline for batched deletion (much faster)
        try:
            # Client is guaranteed non-None here (checked by queue_client availability)
            if queue_client.client is None:
                logger.error(
                    "queue_client_unexpectedly_none",
                    attempted_action="reset_queue_stats",
                )
                return {"status": "error", "message": "Queue client unavailable"}

            # 1. Collect all keys using KEYS (acceptable for reset operations)
            task_keys = await queue_client.client.keys("queue:task:*")
            result_keys = await queue_client.client.keys("queue:result:*")
            pending_keys = await queue_client.client.keys("queue:*:pending")
            running_keys = await queue_client.client.keys("queue:*:running")
            health_keys = await queue_client.client.keys("queue:*:health")

            logger.info(
                "queue_keys_collected_for_deletion",
                task_count=len(task_keys),
                result_count=len(result_keys),
                pending_count=len(pending_keys),
                running_count=len(running_keys),
                health_count=len(health_keys),
            )

            # 2. Use pipeline for batched deletion (non-blocking)
            pipeline = queue_client.client.pipeline()

            # Delete all task records
            for key in task_keys:
                pipeline.unlink(key)  # UNLINK is faster than DELETE (non-blocking)
            stats["tasks_deleted"] = len(task_keys)

            # Delete all result records
            for key in result_keys:
                pipeline.unlink(key)
            stats["results_deleted"] = len(result_keys)

            # Delete all queue sorted sets and running/health keys
            for key in (*pending_keys, *running_keys, *health_keys):
                pipeline.unlink(key)
            stats["queues_cleared"] = len(pending_keys) + len(running_keys) + len(health_keys)

            # Execute all deletions in one batch
            all_keys = task_keys + result_keys + pending_keys + running_keys + health_keys
            if all_keys:
                await pipeline.execute()
                logger.info(
                    "queue_keys_deleted",
                    tasks_deleted=stats["tasks_deleted"],
                    results_deleted=stats["results_deleted"],
                    queues_cleared=stats["queues_cleared"],
                )

        except Exception:
            logger.exception("queue_key_deletion_failed")
            raise

        # 3. Clear all statistics (token counts, costs, task history)
        if queue_client.monitor is not None:
            await queue_client.monitor.clear_all_stats()
            logger.info("queue_statistics_cleared")
        else:
            logger.warning("queue_monitor_not_available")

        logger.info("queue_reset_complete", stats=stats)

        return {
            "status": "success",
            "message": "Queue system reset successfully",
            **stats,
        }

    async def reset_knowledge_base(self) -> dict[str, Any]:
        """Reset entire knowledge base (combined reset).

        Deletes:
        - Import history (Source records)
        - Knowledge graph (nodes, edges, templates)
        - Document sources (sources, chunks, citations, tags)
        - Uploaded files (imports directory)
        - Search indices

        Preserves:
        - Workflows, tools, triggers
        - Conversations
        - Queue statistics

        SRP: Orchestrates multiple reset operations.
        """
        from chaoscypher_core.app_config import get_settings

        settings = get_settings()
        database_name = self.database_name
        total_stats: dict[str, object] = {}

        from pathlib import Path

        db_path = (
            Path(settings.paths.data_dir)
            / settings.paths.databases_subdir
            / database_name
            / settings.paths.app_db_filename
        )
        logger.info(
            "knowledge_base_reset_started",
            database_name=database_name,
            db_path=str(db_path),
            db_exists=db_path.exists(),
            settings_current_database=settings.current_database,
        )

        _delete_source_data(database_name, total_stats)
        _reset_knowledge_graph(database_name, total_stats)
        await _delete_import_files(settings, total_stats)
        _reset_search_indices(database_name, total_stats)

        logger.info("knowledge_base_reset_complete", stats=total_stats)

        return {
            "status": "success",
            "message": "Knowledge base reset successfully",
            **total_stats,
        }

    async def reset_all(self) -> dict[str, Any]:
        """Nuclear option - delete app.db and reinitialize.

        WARNING: This operation cannot be undone!

        SRP: Delegates to DatabaseResetService.
        """
        return await self.database_reset.reset_all()

    def seed_templates(self) -> dict[str, Any]:
        """Re-seed default system templates.

        Safe operation - only creates templates that don't exist. If seeding
        fails, the exception propagates to the global exception handler and
        the client receives a sanitized 5xx response; the traceback is logged
        server-side via ``logger.exception``.
        """
        from chaoscypher_core.database.seed import seed_default_templates

        settings = self.settings_manager.get_settings()

        logger.info("seed_templates_starting", database_name=settings.current_database)

        try:
            seed_default_templates(settings.current_database)
        except Exception:
            logger.exception("seed_templates_failed")
            raise

        logger.info("seed_templates_completed")
        return {
            "status": "success",
            "message": "Default templates seeded successfully",
        }
