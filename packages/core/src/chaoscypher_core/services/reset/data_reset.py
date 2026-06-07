# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Data Reset Service.

Handles reset operations for data storage components:
- Source processing history
- Chats and messages

Extracted from ResetService to follow Single Responsibility Principle.
"""

import shutil
from typing import Any

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.database.adapter_factory import get_sqlite_adapter


logger = structlog.get_logger(__name__)


class DataResetService:
    """Reset service for data storage components.

    Responsibility: Reset source_processing history and chats.
    """

    def __init__(self, database_name: str):
        """Initialize data reset service.

        Args:
            database_name: Name of the database

        """
        self.database_name = database_name

    def reset_source_processing_history(self) -> dict[str, Any]:
        """Reset source_processing history comprehensively.

        Deletes all source_processing-related data in correct FK order:
        - Staged document chunks (``source_id IS NULL``)
        - Source entity embeddings
        - Chunk extraction tasks
        - Chunk extraction jobs
        - Source files
        - Uploaded import files directory

        Returns:
            Dictionary with reset statistics

        """
        logger.info(
            "source_processing_history_reset_started",
            database_name=self.database_name,
        )

        settings = get_settings()
        stats: dict[str, Any] = {"status": "success"}

        adapter = get_sqlite_adapter(database_name=self.database_name)
        try:
            with adapter.transaction():
                # Count before deletion
                source_processing_count = adapter.count_sources(database_name=self.database_name)
                staged_chunks_count = adapter.count_staged_chunks(database_name=self.database_name)
                embeddings_count = adapter.count_embeddings()
                tasks_count = adapter.count_extraction_tasks(database_name=self.database_name)
                jobs_count = adapter.count_extraction_jobs(database_name=self.database_name)

                # Delete in correct FK order (children first)
                # 1. Delete staged chunks (not committed to sources)
                adapter.delete_staged_chunks(database_name=self.database_name)
                logger.info("deleted_staged_chunks", count=staged_chunks_count)

                # 2. Delete entity embeddings (CASCADE from source_file but explicit)
                adapter.clear_all_embeddings()
                logger.info(
                    "deleted_source_processing_entity_embeddings",
                    count=embeddings_count,
                )

                # 3. Delete extraction tasks (CASCADE from job but explicit)
                adapter.delete_extraction_tasks(database_name=self.database_name)
                logger.info("deleted_chunk_extraction_tasks", count=tasks_count)

                # 4. Delete extraction jobs
                adapter.delete_extraction_jobs(database_name=self.database_name)
                logger.info("deleted_chunk_extraction_jobs", count=jobs_count)

                # 5. Delete source files
                adapter.delete_all_sources(database_name=self.database_name)
                logger.info("deleted_source_files", count=source_processing_count)

                stats["source_files_deleted"] = source_processing_count
                stats["staged_chunks_deleted"] = staged_chunks_count
                stats["entity_embeddings_deleted"] = embeddings_count
                stats["extraction_tasks_deleted"] = tasks_count
                stats["extraction_jobs_deleted"] = jobs_count
        finally:
            adapter.disconnect()

        # Delete uploaded import files directory
        try:
            imports_dir = settings.database_dir / settings.paths.imports_subdir
            if imports_dir.exists():
                shutil.rmtree(imports_dir)
                stats["imports_directory_deleted"] = True
                logger.info("imports_directory_deleted", imports_dir=str(imports_dir))
            else:
                stats["imports_directory_deleted"] = False
        except Exception as e:
            logger.exception("imports_directory_delete_failed", error=str(e))
            stats["imports_directory_deleted"] = False

        logger.info("source_processing_history_reset_complete", stats=stats)

        return stats

    def reset_chats(self) -> dict[str, Any]:
        """Reset chats.

        Drops all chats and messages (messages deleted first due to FK).

        Returns:
            Dictionary with reset statistics

        """
        logger.info("chats_reset_started", database_name=self.database_name)

        adapter = get_sqlite_adapter(database_name=self.database_name)
        try:
            with adapter.transaction():
                # List chat IDs for the message delete, then count + delete chats.
                # Protocol does not expose list_chat_ids directly; use list_chats
                # and project to ids — reset is infrequent so the list-overhead
                # is acceptable.
                chats = adapter.list_chats(database_name=self.database_name)
                chat_ids = [c["id"] for c in chats]
                chat_count = len(chat_ids)

                if chat_ids:
                    adapter.delete_messages_by_chat_ids(chat_ids=chat_ids)
                adapter.delete_all_chats(database_name=self.database_name)
        finally:
            adapter.disconnect()

        logger.info("chats_reset_complete", chats_deleted=chat_count)

        return {
            "status": "success",
            "chats_deleted": chat_count,
        }
