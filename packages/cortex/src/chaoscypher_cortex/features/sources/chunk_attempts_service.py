# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Service: list / fetch chunk_extraction_attempts history rows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chaoscypher_core.exceptions import NotFoundError


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


class ChunkAttemptsService:
    """Read-only access to chunk_extraction_attempts."""

    def __init__(self, *, adapter: SqliteAdapter, database_name: str) -> None:
        """Bind to a SQLite adapter scoped to ``database_name``."""
        self._adapter = adapter
        self._database_name = database_name

    async def list_attempts(self, *, source_id: str, chunk_index: int) -> list[dict[str, Any]]:
        """List prior attempts for one chunk (summary fields)."""
        task_id = self._resolve_chunk_task_id(source_id, chunk_index)
        return self._adapter.list_chunk_attempts(chunk_task_id=task_id)

    async def get_attempt(
        self, *, source_id: str, chunk_index: int, attempt_id: str
    ) -> dict[str, Any]:
        """Fetch one attempt's full body."""
        # Validate source/chunk first so callers get consistent 404s
        self._resolve_chunk_task_id(source_id, chunk_index)
        attempt = self._adapter.get_chunk_attempt(attempt_id)
        if attempt is None:
            raise NotFoundError("chunk_attempt", attempt_id)
        return attempt

    def _resolve_chunk_task_id(self, source_id: str, chunk_index: int) -> str:
        """Resolve ``(source_id, chunk_index)`` to a chunk_task id.

        Raises ``NotFoundError`` if the source, its current extraction job, or
        the chunk slot does not exist.
        """
        source = self._adapter.get_source(source_id, self._database_name)
        if source is None:
            raise NotFoundError("source", source_id)
        job_id = source.get("current_extraction_job_id")
        if not job_id:
            raise NotFoundError("extraction_job", f"source {source_id} has no extraction job")
        task = self._adapter.get_chunk_task_by_job_and_index(
            job_id=job_id,
            chunk_index=chunk_index,
            database_name=self._database_name,
        )
        if task is None:
            raise NotFoundError("chunk_task", f"chunk_index={chunk_index} in job {job_id}")
        task_id: str = task["id"]
        return task_id
