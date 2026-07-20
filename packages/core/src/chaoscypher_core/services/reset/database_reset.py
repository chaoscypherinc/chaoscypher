# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Database Reset Service.

Handles nuclear reset operation - complete database deletion and reinitialization.

Extracted from ResetService to follow Single Responsibility Principle.
"""

import shutil
from pathlib import Path
from typing import Any

import structlog

from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
from chaoscypher_core.database.engine import get_db_path, init_database
from chaoscypher_core.queue import queue_client


logger = structlog.get_logger(__name__)


class DatabaseResetService:
    """Reset service for complete database reinitialization.

    Responsibility: Delete app.db and reinitialize with defaults (nuclear option).
    """

    def __init__(self, database_name: str):
        """Initialize database reset service.

        Args:
            database_name: Name of the database

        """
        self.database_name = database_name
        self.settings = get_settings()

    async def reset_all(self) -> dict[str, Any]:
        """Nuclear option — delete the database and reinitialize.

        This removes ALL data and recreates from scratch:
        - app.db (SQLite database, includes graph + search indices)
        - graphs/ directory (residual from earlier deployments; removed if present)
        - imports/ (uploaded files)
        - Queue history (task records and recent lists)

        Returns:
            Dictionary with reset statistics

        """
        logger.warning("database_reset_initiated", database_name=self.database_name)

        # CRITICAL: Dispose of cached engine before deleting database file
        # This closes all connections and prevents "readonly database" errors
        db_path = get_db_path(self.database_name)
        evict_engine(db_path)

        # Get database directory path using centralized settings
        db_dir = (
            Path(self.settings.paths.data_dir)
            / self.settings.paths.databases_subdir
            / self.database_name
        )

        # Delete app.db and SQLite WAL files (db_path already resolved above)
        if db_path.exists():
            db_path.unlink()
            logger.info("database_file_deleted", db_path=str(db_path))

        # Also delete WAL files to ensure clean state
        wal_path = db_path.with_suffix(".db-wal")
        shm_path = db_path.with_suffix(".db-shm")
        for wal_file in [wal_path, shm_path]:
            if wal_file.exists():
                wal_file.unlink()
                logger.info("wal_file_deleted", wal_path=str(wal_file))

        # Remove residual graphs/ directory if present (from earlier deployments)
        graphs_dir = db_dir / "graphs"
        if graphs_dir.exists():
            shutil.rmtree(graphs_dir)
            logger.info("graphs_directory_deleted", graphs_dir=str(graphs_dir))

        # Delete import files
        imports_dir = db_dir / "imports"
        if imports_dir.exists():
            shutil.rmtree(imports_dir)
            logger.info("imports_directory_deleted", imports_dir=str(imports_dir))

        # Clear queue history
        logger.info("clearing_queue_history")
        if queue_client.is_available and queue_client.client is not None:
            valkey = queue_client.client
            try:
                # Clear all task records and recent lists
                await queue_client.clear_all_stats()

                # Also delete all task records for safety
                deleted_tasks = 0
                async for key in valkey.scan_iter(match="queue:task:*"):
                    await valkey.delete(key)
                    deleted_tasks += 1

                # Delete all result records
                async for key in valkey.scan_iter(match="queue:result:*"):
                    await valkey.delete(key)

                logger.info("queue_history_cleared", deleted_tasks=deleted_tasks)
            except Exception as e:
                logger.warning(
                    "queue_clear_failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
        else:
            logger.warning("queue_client_unavailable_skipping_cleanup")

        # Adapter cache cleanup no longer needed — adapters are per-request
        # and cleaned up by AdapterCleanupMiddleware

        # Clear search repository cache (graph repository no longer uses caching)
        try:
            from chaoscypher_core.repo_factories.search_factory import (
                invalidate_search_repository,
            )

            # SQLite-backed graph repository doesn't use caching - data already deleted via rmtree
            # Only need to clear search repository cache
            invalidate_search_repository()
            logger.info("search_repository_cache_cleared")
        except Exception as e:
            logger.warning("search_repository_cache_clear_failed", error=str(e))

        # Reinitialize database (will create a new engine and fresh graphs)
        # This also seeds default templates via seed_default_data()
        init_database(self.database_name)

        # Count what was created after reinitialization
        adapter = get_sqlite_adapter(database_name=self.database_name)
        try:
            workflows_created = adapter.count_workflows(database_name=self.database_name)
            system_tools_created = adapter.count_system_tools()
            triggers_created = adapter.count_triggers(database_name=self.database_name)
        finally:
            adapter.disconnect()

        logger.warning("database_reset_complete")

        return {
            "status": "complete",
            "action": "Database deleted and reinitialized",
            "workflows_created": workflows_created,
            "system_tools_created": system_tools_created,
            "triggers_created": triggers_created,
        }
