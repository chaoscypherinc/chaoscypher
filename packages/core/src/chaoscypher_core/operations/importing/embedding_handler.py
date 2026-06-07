# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chunk-embedding handler for the LLM queue.

Provides the ``handle_embed_chunks`` async function that generates
vector embeddings for the unembedded chunks of an already-indexed source
and finalizes the indexing stage (chunk count, embedding metadata,
``task_completed`` event, optional analysis queueing).

This handler runs on ``QUEUE_LLM`` rather than ``QUEUE_OPERATIONS``
because embedding is the LLM-bound tail of the indexing pipeline.
Separating it lets the ops queue keep its high concurrency for the
cheap load/chunk/persist stage while embedding serializes behind the
LLM-provider's concurrency budget.

Idempotency contract: ``DocumentChunk.embedded_at`` is the resume
checkpoint. A crash between the vector-index write and the ``embedded_at``
update leaves a chunk in the "re-embed me" state on the next attempt —
wasted work, not correctness loss. This is why the operation is
registered with ``retry_on_crash=True``.

Called by ``ImportOperationsService._embed_chunks_handler`` with shared
services cached on the worker.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.queue_utils import (
    queue_import_analysis,
)
from chaoscypher_core.services.events import event_bus
from chaoscypher_core.services.sources import source_heartbeat
from chaoscypher_core.services.stage_progress import StageName, StageProgress


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.app_config import Settings

logger = structlog.get_logger(__name__)


async def _embed_unembedded_chunks(
    *,
    source_id: str,
    database_name: str,
    adapter: SqliteAdapter,
    indexing_service: Any,
    progress_callback: Any = None,
    cancellation_check: Any = None,
    wave_size: int | None = None,
) -> int:
    """Embed any chunks for this source that don't yet have embeddings.

    Uses ``DocumentChunk.embedded_at`` as the checkpoint: chunks with a
    non-NULL timestamp are considered done and skipped. Called both on
    the initial embedding dispatch and on source-reconciler re-dispatch
    after a crash, so the caller sees the same semantics either way.

    Cost / resource-exhaustion fix (2026-05-25): chunks are keyset-paginated
    and embedded in bounded *waves* rather than loading every unembedded chunk
    (and its ``content``) into memory at once — a multi-GB document would
    otherwise OOM the worker. Peak memory is bounded by one wave. Each wave is
    embedded, then marked ``embedded_at``, then the keyset cursor advances; the
    persist-then-mark ordering means a crash between the two re-embeds that
    wave on the next attempt (wasted work, not correctness loss). Marking per
    wave also tightens crash recovery — completed waves are skipped on resume.

    Args:
        source_id: Source being embedded.
        database_name: Active database name.
        adapter: SqliteAdapter implementing list_unembedded_chunks,
            count_unembedded_chunks and mark_chunks_embedded.
        indexing_service: IndexingService exposing embed_chunks(chunks, ...).
        progress_callback: Optional callback(processed, total) forwarded
            to the embedding provider for UI progress.
        cancellation_check: Optional async callable returning True to abort
            the embedding wave, forwarded to the embedding provider.
        wave_size: Chunks per wave. ``None`` (default) reads
            ``batching.embedding_wave_size`` from the engine settings.

    Returns:
        Number of chunks newly embedded in this call. Zero means either
        nothing to do (all already embedded) or the source has no chunks.
    """
    total = adapter.count_unembedded_chunks(source_id=source_id, database_name=database_name)
    if total == 0:
        logger.info(
            "embedding_skipped_all_done",
            source_id=source_id,
            database_name=database_name,
        )
        return 0

    if wave_size is None:
        from chaoscypher_core.app_config import get_settings

        wave_size = get_settings().batching.embedding_wave_size

    logger.info(
        "embedding_starting",
        source_id=source_id,
        count=total,
        wave_size=wave_size,
    )

    # F28: cross-check that the embedding model's output dimension still
    # matches whatever was recorded on the source's first embedding pass.
    # First-pass embedding (no row, or ``embedding_dimensions IS NULL``)
    # skips the check — there is nothing to compare against. Re-embedding
    # passes (recovery, re-index after model swap) get the per-source
    # guard so a mid-flight model change cannot silently corrupt the
    # chunk vector index. Wider settings-vs-actual validation is F35's
    # responsibility (separate guard layered over this one).
    expected_dimensions: int | None = None
    source_row = adapter.get_source(source_id, database_name)
    if source_row is not None:
        expected_dimensions = source_row.get("embedding_dimensions")

    # Wrap the embedding stage in StageProgress so the user-facing "when is
    # my source searchable?" indicator reflects real chunk-embedding progress.
    # ``total`` is the whole-document count fixed up front; each wave ticks
    # ``processed`` toward it. Embedding runs wave-by-wave so peak memory is
    # bounded by ``wave_size`` chunks rather than the whole document.
    embedded_total = 0
    dimensions_recorded = False
    async with StageProgress(
        storage=adapter,
        parent_id=source_id,
        stage=StageName.EMBEDDING,
        total=total,
    ) as stage_progress:
        # Keyset cursor over chunk_index (unique per source) — guarantees
        # forward progress and termination regardless of marking, so a
        # too-large document can never spin or re-fetch a wave.
        after_chunk_index: int | None = None
        while True:
            wave = adapter.list_unembedded_chunks(
                source_id=source_id,
                database_name=database_name,
                after_chunk_index=after_chunk_index,
                limit=wave_size,
            )
            if not wave:
                break

            # Defense-in-depth: the keyset MUST move forward. A correct adapter
            # returns chunks strictly past ``after_chunk_index``, so the cursor
            # always advances and the loop terminates. If one ever returns a
            # page that does not advance it, break (loudly) BEFORE re-embedding
            # the same wave — that stall otherwise spins the worker
            # indefinitely at multi-GB memory.
            if after_chunk_index is not None and wave[-1]["chunk_index"] <= after_chunk_index:
                logger.error(
                    "embedding_wave_cursor_stalled",
                    source_id=source_id,
                    after_chunk_index=after_chunk_index,
                    wave_last_chunk_index=wave[-1]["chunk_index"],
                )
                break

            count = await indexing_service.embed_chunks(
                chunks=wave,
                source_id=source_id,
                database_name=database_name,
                progress_callback=progress_callback,
                cancellation_check=cancellation_check,
                expected_dimensions=expected_dimensions,
            )

            # First-pass dim recording (Phase 7 audit-remediation P1 #7):
            # record embedding_dimensions on the SourceRow after the first
            # wave's embeddings succeed but BEFORE we mark that wave embedded,
            # so a crash in between leaves the source re-embeddable with the
            # dim already on the row (the next attempt cross-checks rather than
            # blindly accepting whatever settings.search.vector_dimensions says
            # at that point). Done once; idempotent (only when dim is unset);
            # failure is logged and swallowed — it never blocks the pipeline.
            if not dimensions_recorded and source_row is not None and expected_dimensions is None:
                first_pass_dim = indexing_service.settings.search.vector_dimensions
                try:
                    adapter.update_source_columns(
                        source_id=source_id,
                        database_name=database_name,
                        updates={"embedding_dimensions": first_pass_dim},
                    )
                except Exception:
                    logger.warning(
                        "embedding_dimensions_record_failed",
                        source_id=source_id,
                        exc_info=True,
                    )
            dimensions_recorded = True

            # Mark this wave AFTER its embeddings are persisted to the vector
            # index so a crash between vector-write and DB-update re-embeds
            # only this wave (waste of work, not correctness issue).
            adapter.mark_chunks_embedded(
                chunk_ids=[c["id"] for c in wave],
                embedded_at=datetime.now(UTC),
                database_name=database_name,
            )

            # Tick once per embedded chunk. StageProgress._safe swallows
            # individual tick failures so the pipeline never stalls on
            # progress reporting.
            for _ in range(count):
                await stage_progress.tick()

            embedded_total += count
            after_chunk_index = wave[-1]["chunk_index"]

    logger.info(
        "embedding_complete",
        source_id=source_id,
        count=embedded_total,
    )
    return embedded_total


async def handle_embed_chunks(
    data: dict[str, Any],
    source_repository: Any,
    indexing_service: Any,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Embed unembedded chunks and finalize the indexing stage.

    Workflow:
        1. Pause-guard fast exit.
        2. Under a source heartbeat, fetch unembedded chunks from the DB
           and generate embeddings via ``indexing_service.embed_chunks``.
        3. Read final chunk count + embedding metadata and call
           ``adapter.complete_indexing`` to mark the source INDEXED.
        4. Clear the step-progress banner and emit ``task_completed``.
        5. If ``SourceRow.auto_analyze`` is true, enqueue
           ``OP_IMPORT_ANALYSIS`` on the ops queue — preserves the
           previous end-of-pipeline behavior, just triggered from a
           later handler. Reads the flag from the database (not the queue
           payload) so recovery paths that rebuild ``file_info`` from a
           narrower projection still trigger analysis (F31).

    The payload carries only IDs (``source_id``, ``file_info``). Chunks
    are fetched from the database by the handler rather than traveling
    through the queue, so the queue payload stays small even for large
    documents.

    Args:
        data: Task data — must contain ``source_id`` and ``file_info``.
        source_repository: SqliteAdapter implementing storage protocols.
        indexing_service: IndexingService cached at worker level.
        metadata: Task metadata (unused here but part of handler contract).
        task_id: Queue task ID — used to poll cancellation.

    Returns:
        Result dictionary with chunks count, embedding model, and status.

    Raises:
        ValidationError: If ``file_info`` is missing from the task data, or if
            ``source_id`` is not a string.
    """
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.operations.pause_guard import check_paused

    source_id = data.get("source_id")
    file_info = data.get("file_info")
    if file_info is None:
        msg = "file_info is required"
        raise ValidationError(msg, field="file_info")

    if not isinstance(source_id, str):
        msg = "source_id must be a string"
        raise ValidationError(msg, field="source_id")

    logger.info(
        "embed_chunks_operation_processing",
        source_id=source_id,
    )

    settings = get_settings()
    database_name = settings.current_database
    adapter = source_repository

    # Pause guard: if the source or the system is paused, return
    # {"skipped": "paused"} without touching any real work. Paused is
    # NOT an error — the worker frees up immediately and picks up the
    # next queued task.
    pause_check = check_paused(
        source_id=source_id,
        database_name=database_name,
        adapter=adapter,
    )
    if pause_check.paused:
        logger.info(
            "handler_skipped_paused",
            handler="handle_embed_chunks",
            source_id=source_id,
            scope=pause_check.scope,
            reason=pause_check.reason,
        )
        return {"skipped": "paused"}

    # Source liveness heartbeat — keeps last_activity_at fresh while
    # the embedding wave is running so the source-recovery reconciler
    # does not treat a long embedding pass (minutes on large documents)
    # as a stall and dispatch a duplicate handler.
    async with source_heartbeat(
        adapter=adapter,
        source_id=source_id,
        database_name=database_name,
    ):
        return await _run_embedding(
            source_id=source_id,
            file_info=file_info,
            adapter=adapter,
            indexing_service=indexing_service,
            settings=settings,
            database_name=database_name,
            task_id=task_id,
        )


async def _run_embedding(
    *,
    source_id: str,
    file_info: dict[str, Any],
    adapter: Any,
    indexing_service: Any,
    settings: Any,
    database_name: str,
    task_id: str | None,
) -> dict[str, Any]:
    """Inner embedding pipeline body, wrapped by the heartbeat CM."""
    try:
        filename = file_info.get("filename", "unknown")

        # Stage 1/2: generating embeddings (with per-batch progress +
        # cancellation check). The indexing pipeline now has two
        # post-chunk stages — embed, then complete — so the step counter
        # starts at 1/2 rather than 3/4 like the old pre-split pipeline.
        _, total_for_progress = adapter.get_chunks_by_source(source_id, page=1, page_size=1)
        adapter.update_step_progress(
            source_id,
            1,
            2,
            f"Generating embeddings (0/{total_for_progress:,} chunks)",
        )

        def _embedding_progress(processed: int, total: int) -> None:
            """Push a per-chunk progress message into the step progress field."""
            adapter.update_step_progress(
                source_id,
                1,
                2,
                f"Generating embeddings ({processed:,}/{total:,} chunks)",
            )

        async def _check_cancelled() -> bool:
            """Return True if the embedding task has been cancelled in the queue."""
            from chaoscypher_core.queue import queue_client

            if task_id and queue_client.client:
                return await queue_client.is_task_cancelled(task_id)
            return False

        # Generate embeddings for any unembedded chunks. Uses
        # DocumentChunk.embedded_at as the resume checkpoint so a crash
        # halfway through embedding resumes at the first unembedded chunk
        # on the next attempt instead of starting over.
        embedded_count = await _embed_unembedded_chunks(
            source_id=source_id,
            database_name=database_name,
            adapter=adapter,
            indexing_service=indexing_service,
            progress_callback=_embedding_progress,
            cancellation_check=_check_cancelled,
        )

        # Stage 2/2: completing indexing
        adapter.update_step_progress(source_id, 2, 2, "Completing indexing")

        # Derive total chunk count and embedding metadata from the
        # database rather than the embed return value so this code path
        # behaves identically on first-run and resume.
        _, total_chunks = adapter.get_chunks_by_source(source_id, page=1, page_size=1)

        # Race guard (2026-05-22): chunks=0 here means the source row +
        # all its chunks were CASCADE-deleted while we were in
        # ``_embed_unembedded_chunks`` (typical trigger: operator clicks
        # Delete in the UI mid-embedding). Don't try to mark indexing
        # complete or queue downstream analysis — that codepath emits
        # ``embedding_dimensions_record_failed`` +
        # ``embed_chunks_source_row_missing_at_finalize`` errors as it
        # walks a deleted source. Exit cleanly with one info log.
        if total_chunks == 0:
            logger.info(
                "embed_chunks_source_deleted_mid_flight",
                source_id=source_id,
                database_name=database_name,
                embedded_this_pass=embedded_count,
            )
            return {
                "success": True,
                "source_id": source_id,
                "file_id": source_id,
                "status": "deleted",
                "chunks_count": 0,
                "embedded_this_pass": embedded_count,
                "embedding_model": indexing_service.settings.embedding.model,
            }

        embedding_model = indexing_service.settings.embedding.model
        embedding_dimensions = indexing_service.settings.search.vector_dimensions

        # Mark indexing stage as complete
        adapter.complete_indexing(
            source_id=source_id,
            chunks_count=total_chunks,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )

        logger.info(
            "embed_chunks_completed",
            source_id=source_id,
            chunks_count=total_chunks,
            embedded_this_pass=embedded_count,
            embedding_model=embedding_model,
        )

        # Clear step progress after indexing completes
        adapter.update_step_progress(source_id, 0, 0, "")

        event_bus.emit(
            "task_completed",
            action=f"Indexing complete: {filename}",
            source="worker",
            details={"source_id": source_id, "filename": filename, "chunks": total_chunks},
            database_name=database_name,
        )

        # Queue analysis operation AFTER indexing completes to avoid
        # race condition: analysis needs hierarchical groups committed
        # by the chunking stage AND embeddings committed by this stage.
        #
        # F31: read ``auto_analyze`` from the SourceRow rather than the
        # queue payload's ``file_info``. ``SourceRow.auto_analyze`` is the
        # authoritative store (set at upload time, also consulted by the
        # source reconciler). Reading from the payload silently lost
        # auto-analysis whenever the recovery / re-dispatch path rebuilt
        # ``file_info`` without re-populating the flag — sources got stuck
        # at INDEXED with no follow-on extraction and no error.
        source_row = adapter.get_source(source_id, database_name)
        if source_row is None:
            # The row was loaded earlier in this same handler chain (see
            # ``handle_embed_chunks`` → ``_run_embedding``); a missing row
            # here is a hard inconsistency, not a graceful-degradation case.
            # Fall back to the payload so the dispatch is still attempted,
            # but log loudly so the inconsistency surfaces in observability.
            logger.error(
                "embed_chunks_source_row_missing_at_finalize",
                source_id=source_id,
                database_name=database_name,
            )
            should_auto_analyze = bool(file_info.get("auto_analyze"))
        else:
            should_auto_analyze = bool(source_row.get("auto_analyze"))

        if should_auto_analyze:
            await _queue_post_indexing_analysis(
                source_id,
                file_info,
                settings,
                database_name=database_name,
                source_row=source_row,
            )

        return {
            "success": True,
            "source_id": source_id,
            "file_id": source_id,
            "status": SourceStatus.INDEXED,
            "chunks_count": total_chunks,
            "embedded_this_pass": embedded_count,
            "embedding_model": embedding_model,
        }

    except Exception as exc:
        logger.exception(
            "embed_chunks_failed",
            source_id=source_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

        # Rollback the session to clear any pending transaction errors
        # (e.g., IntegrityError leaves session in PendingRollbackError state)
        try:
            adapter.session.rollback()
        except Exception:
            logger.debug("session_rollback_failed_before_fail_indexing", source_id=source_id)

        adapter.fail_indexing(source_id, str(exc))

        raise

    # No finally/disconnect - singleton adapter is long-lived


async def _queue_post_indexing_analysis(
    source_id: str,
    file_info: dict[str, Any],
    settings: Settings,
    *,
    database_name: str,
    source_row: dict[str, Any] | None = None,
) -> None:
    """Queue the analysis operation after indexing completes successfully.

    Exceptions are NOT swallowed. If the enqueue fails, the error
    propagates to ``_run_embedding``'s outer handler, which calls
    ``fail_indexing`` and re-raises so the queue's ``retry_on_crash``
    path takes over. Embedding is idempotent (``DocumentChunk.embedded_at``
    gates), so a retry doesn't redo LLM work — it just re-attempts the
    enqueue. A persistent programming bug therefore surfaces as a visible
    error row after retries exhaust, not as a source silently stuck at
    ``indexed``.

    Args:
        source_id: Source file ID.
        file_info: File information dictionary.
        settings: Application settings for priority configuration.
        database_name: Target database — required for cancel-by-metadata
            scoping on the enqueued task.
        source_row: Authoritative source row (W1, 2026-05-07). When
            present, drives the analysis depth and generate-embeddings
            decision so the next task picks up the user's persisted
            choice rather than the queue payload mirror.
    """
    # The source row is canonical for upload settings (W1). Fall back
    # to the payload only for legacy paths where the row predates the
    # column or hasn't been loaded.
    if source_row is not None:
        analysis_depth = (
            source_row.get("extraction_depth") or file_info.get("analysis_depth") or "full"
        )
    else:
        analysis_depth = file_info.get("analysis_depth", "full")

    analysis_task_id = await queue_import_analysis(
        file_id=source_id,
        file_info=file_info,
        analysis_depth=analysis_depth,
        database_name=database_name,
        generate_embeddings=file_info.get("generate_embeddings", True),
        priority=settings.priorities.background,
    )
    logger.info(
        "analysis_queued_after_indexing",
        source_id=source_id,
        analysis_task_id=analysis_task_id,
    )
