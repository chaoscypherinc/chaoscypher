# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Service: rerun one chunk on a (typically committed) source.

The service validates the source + chunk state, atomically snapshots
the chunk task into chunk_extraction_attempts + wipes the chunk_task
row + walks the source status back to ``extracting``, then enqueues a
fresh OP_EXTRACT_CHUNK. The existing chunk handler, finalize handler,
and commit handler take over from there (no new operation type — CC044
unchanged).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import OP_EXTRACT_CHUNK, QUEUE_LLM
from chaoscypher_core.exceptions import ConflictError, NotFoundError
from chaoscypher_core.services.quality.counters import (
    QualityCounter,
    increment_quality_counter,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.queue.client import QueueClient


logger = structlog.get_logger(__name__)


class ChunkRerunService:
    """Re-extract a single chunk on a (typically committed) source."""

    def __init__(
        self,
        *,
        adapter: SqliteAdapter,
        queue_client: QueueClient,
        database_name: str,
    ) -> None:
        """Bind to a SQLite adapter and queue client scoped to ``database_name``."""
        self._adapter = adapter
        self._queue_client = queue_client
        self._database_name = database_name

    async def rerun_chunk(self, *, source_id: str, chunk_index: int) -> dict[str, Any]:
        """Reset one chunk's task and re-enqueue chunk extraction.

        Returns:
            ``{"chunk_task_id", "queue_task_id", "attempt_number",
            "source_status"}``.

        Raises:
            NotFoundError: source / chunk_task does not exist.
            ConflictError: source is in ``committing``; chunk task is in
                ``pending`` / ``queued`` / ``running``; atomic reset
                lost a race.
        """
        # 1. Source lookup + status guard
        source = self._adapter.get_source(source_id, self._database_name)
        if source is None:
            raise NotFoundError("source", source_id)
        if source.get("status") == "committing":
            raise ConflictError("Source is currently committing — try again in a moment")

        # 2. Chunk task lookup + status guard. Look up by (source_id, chunk_index)
        # via the job join — ``source.current_extraction_job_id`` is cleared at
        # extraction-complete time so the active-job pointer is None on every
        # committed source. The chunk_task / chunk_extraction_jobs rows persist
        # past commit, so the join finds them. Most-recent-job ordering covers
        # the multi-job case (force_re_extract).
        task = self._adapter.get_chunk_task_by_source_and_index(
            source_id=source_id, chunk_index=chunk_index, database_name=self._database_name
        )
        if task is None:
            raise NotFoundError("chunk_task", f"chunk_index={chunk_index} on source {source_id}")
        if task.get("status") in ("pending", "queued", "running"):
            msg = f"chunk {chunk_index} is already being processed"
            raise ConflictError(msg)
        job_id = task["job_id"]

        # 3. Atomic snapshot + wipe + source walk-back. May raise
        # ConflictError (rowcount=0 race lost) or NotFoundError.
        attempt_number = self._adapter.reset_chunk_task_for_rerun(
            task_id=task["id"],
            source_id=source_id,
        )

        # 4. Quality counter (best-effort — never blocks the rerun)
        await increment_quality_counter(
            adapter=self._adapter,
            source_id=source_id,
            database_name=self._database_name,
            counter=QualityCounter.CHUNKS_RERUN_TOTAL,
        )

        # 5. Enqueue OP_EXTRACT_CHUNK with the existing payload shape.
        # If this raises (e.g., queue down), we deliberately do NOT roll
        # back the DB reset — the SourceRecovery reconciler will detect
        # the orphan pending chunk_task within 60s and re-dispatch.
        queue_task_id = await self._queue_client.enqueue_task(
            queue=QUEUE_LLM,
            operation=OP_EXTRACT_CHUNK,
            data={
                "chunk_task_id": task["id"],
                "job_id": job_id,
                "database_name": self._database_name,
                "chunk_index": chunk_index,
                "small_chunk_ids": task.get("small_chunk_ids") or [],
            },
            metadata={
                "job_id": job_id,
                "source_id": source_id,
                "operation_type": OP_EXTRACT_CHUNK,
                "rerun": True,
                "attempt_number": attempt_number,
            },
        )

        logger.info(
            "chunk_rerun_initiated",
            source_id=source_id,
            chunk_index=chunk_index,
            chunk_task_id=task["id"],
            attempt_number=attempt_number,
            queue_task_id=queue_task_id,
            from_status=source.get("status"),
            to_status="extracting",
        )

        return {
            "chunk_task_id": task["id"],
            "queue_task_id": queue_task_id,
            "attempt_number": attempt_number,
            "source_status": "extracting",
        }
