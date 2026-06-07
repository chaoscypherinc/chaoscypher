# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction Finalizer - results aggregation, deduplication, and commit queuing.

Handles the finalization phase of distributed chunk extraction:

1. Aggregate all completed chunk results
2. Deduplicate entities (exact + semantic, code-only — no LLM during finalization)
3. Normalize, generate embeddings, suggest templates (via Core ExtractionService)
4. Store embeddings, LLM metrics, quality scores
5. Mark extraction complete and queue commit phase
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.llm_queue.queue_service import LLMQueueService as LLMService
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )
    from chaoscypher_core.settings import EngineSettings

import structlog

from chaoscypher_core.constants import (
    OP_FINALIZE_EXTRACTION,
    QUEUE_LLM,
)
from chaoscypher_core.queue import queue_client
from chaoscypher_core.services.events import event_bus
from chaoscypher_core.services.sources import source_heartbeat


logger = structlog.get_logger(__name__)

# Task statuses that count as fully settled — no more state transitions
# expected. Any status NOT in this set means the task is still in-flight.
TERMINAL_TASK_STATES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "orphaned"})


def _aggregate_chunk_errors(failed_tasks: list[dict[str, Any]], *, limit: int = 3) -> str:
    """Build a short human-readable summary from failed ChunkExtractionTask rows.

    Returns the top-N most frequent error_message values formatted as
    "msg (xN)" joined with " | ". Returns "<unknown>" if no messages
    are present (defensive — failed_tasks should always have one).

    Args:
        failed_tasks: List of failed task dicts (each with an ``error_message`` key).
        limit: Maximum distinct messages to include in the summary.

    Returns:
        Human-readable string of the top error messages with occurrence counts.
    """
    from collections import Counter

    messages = [
        (t.get("error_message") or "").strip() for t in failed_tasks if t.get("error_message")
    ]
    if not messages:
        return "<unknown>"

    counts = Counter(messages).most_common(limit)
    parts = [f"{msg} (x{n})" if n > 1 else msg for msg, n in counts]
    return " | ".join(parts)


async def _ensure_chunk_embeddings(
    *,
    adapter: SqliteAdapter,
    completed_tasks: list[dict[str, Any]],
    embedding_service: Any,
    job_id: str,
) -> list[list[float]] | None:
    """Backfill raw_entity_embeddings for any chunk rows missing them.

    Steady state this is a no-op — the chunk handler eager-writes embeddings
    alongside raw_entities. The backfill exists as a safety net for in-flight
    chunk tasks that pre-date the schema change, or for chunks where the
    embedding service was unavailable at extract time.

    Returns the aggregated embeddings in chunk-index order parallel to
    ``aggregate_chunk_results(completed_tasks)["entities"]``, or ``None`` if
    any chunk's embeddings could not be obtained (cache miss → dedup falls
    back to its own batch_embed call).
    """
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        _compute_chunk_entity_embeddings,
    )

    aggregated: list[list[float]] = []
    for task in completed_tasks:
        chunk_entities = task.get("raw_entities") or []
        if not chunk_entities:
            continue
        cached = task.get("raw_entity_embeddings")
        if cached is not None and len(cached) == len(chunk_entities):
            aggregated.extend(cached)
            continue
        # Backfill: compute and persist eagerly so a mid-loop crash keeps
        # what's already been written.
        computed = await _compute_chunk_entity_embeddings(chunk_entities, embedding_service)
        if computed is None or len(computed) != len(chunk_entities):
            logger.info(
                "chunk_embeddings_backfill_unavailable",
                job_id=job_id,
                task_id=task.get("id"),
                entity_count=len(chunk_entities),
            )
            return None
        try:
            adapter.set_chunk_task_embeddings(task["id"], computed)
        except Exception as exc:
            # Persist failure is non-fatal: dedup will recompute. Log and continue.
            logger.warning(
                "chunk_embeddings_persist_failed",
                job_id=job_id,
                task_id=task.get("id"),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        aggregated.extend(computed)
    return aggregated if aggregated else None


# ------------------------------------------------------------------ #
#  Finalization handler
# ------------------------------------------------------------------ #


async def finalize_extraction_handler(
    graph_repository: GraphRepository,
    llm_service: LLMService,
    source_repository: SqliteAdapter,
    chunk_extraction_service: ChunkExtractionOperationsService,
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Finalize extraction: aggregate, dedup, and complete.

    Runs deduplication (code-only, no LLM calls) and proceeds directly
    to completion (normalization, embeddings, templates, commit).

    Idempotent: if the source is already past the extracting phase
    (status ``extracted``, ``committing``, or ``committed``) the handler
    returns a skip result without touching aggregation or storage.
    This is the protective short-circuit for re-dispatch from the
    queue reconciler or source reconciler.

    Args:
        graph_repository: GraphRepository for graph operations.
        llm_service: LLMService for AI operations.
        source_repository: SqliteAdapter for storage.
        chunk_extraction_service: ChunkExtractionOperationsService for queuing.
        data: Task data with job ID and configuration.
        metadata: Task metadata.
        task_id: Queue task ID.

    Returns:
        Result dictionary with final extraction statistics, or a
        ``{"skipped": ...}`` dict if the handler short-circuited.

    """
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.app_config.engine_factory import build_engine_settings

    job_id = data["job_id"]
    source_id = data["source_id"]
    database_name = data["database_name"]
    generate_embeddings = data.get("generate_embeddings", True)

    # --- Restart-safe short-circuit --------------------------------
    # If the source has already moved past extracting (someone — maybe
    # a previous attempt of this same handler — already finalized it),
    # bail out before touching aggregation, dedup, embeddings, or the
    # graph. This is the protection against re-dispatch from either
    # reconciler after a crash.
    source_row = source_repository.get_source(source_id, database_name)
    if source_row is None:
        logger.warning(
            "finalize_source_missing",
            source_id=source_id,
            database_name=database_name,
        )
        return {"skipped": "source_missing"}

    if source_row.get("status") in ("extracted", "committing", "committed"):
        logger.info(
            "finalize_already_done",
            source_id=source_id,
            status=source_row.get("status"),
            job_id=job_id,
        )
        return {"skipped": "already_finalized", "status": source_row.get("status")}

    # Pause guard: runs after the idempotency short-circuit so a
    # finalized source still returns {already_finalized}, but a
    # mid-extraction pause stops the handler from aggregating/deduping.
    # Reuses the already-fetched source_row for the per-source check.
    if source_row.get("is_paused"):
        logger.info(
            "handler_skipped_paused",
            handler="finalize_extraction_handler",
            source_id=source_id,
            job_id=job_id,
            scope="source",
            reason=source_row.get("paused_reason"),
        )
        return {"skipped": "paused"}

    system_state = source_repository.get_system_state()
    if system_state and system_state.get("processing_paused"):
        logger.info(
            "handler_skipped_paused",
            handler="finalize_extraction_handler",
            source_id=source_id,
            job_id=job_id,
            scope="system",
            reason=system_state.get("processing_paused_reason"),
        )
        return {"skipped": "paused"}

    logger.info(
        "finalize_extraction_started",
        job_id=job_id,
        source_id=source_id,
        generate_embeddings=generate_embeddings,
    )

    settings = get_settings()
    engine_settings = build_engine_settings(settings)
    adapter = source_repository

    # Stage entry: zero recovery_attempts so accumulated false-positive
    # recoveries from the extracting stage don't compound into commit and
    # push the counter toward the 10-attempt exhaustion cap on healthy
    # sources. A successful arrival at finalize proves forward progress.
    try:
        adapter.reset_source_recovery_attempts(source_id=source_id, database_name=database_name)
    except Exception as exc:  # reset is best-effort; never block finalization
        logger.warning(
            "reset_recovery_attempts_failed",
            source_id=source_id,
            database_name=database_name,
            stage="finalize_extraction",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    # Source liveness heartbeat — see chaoscypher_core.services.sources.heartbeat.
    # Aggregation, deduplication, embedding, and template generation
    # collectively can run far longer than the 60s reconciler interval
    # for large extractions; the heartbeat keeps last_activity_at fresh
    # so the reconciler does not race a duplicate dispatch.
    async with source_heartbeat(
        adapter=adapter,
        source_id=source_id,
        database_name=database_name,
    ):
        return await _finalize_extraction_inner(
            graph_repository=graph_repository,
            llm_service=llm_service,
            chunk_extraction_service=chunk_extraction_service,
            adapter=adapter,
            data=data,
            job_id=job_id,
            source_id=source_id,
            database_name=database_name,
            generate_embeddings=generate_embeddings,
            settings=settings,
            engine_settings=engine_settings,
        )


async def _finalize_extraction_inner(  # noqa: C901, PLR0912 - finalizer orchestrates ~18 distinct post-extraction steps; refactor is out-of-scope for this PR
    *,
    graph_repository: GraphRepository,
    llm_service: LLMService,
    chunk_extraction_service: ChunkExtractionOperationsService,
    adapter: SqliteAdapter,
    data: dict[str, Any],
    job_id: str,
    source_id: str,
    database_name: str,
    generate_embeddings: bool,
    settings: Settings,
    engine_settings: EngineSettings,
) -> dict[str, Any]:
    """Inner finalization body — wrapped by source_heartbeat in the public handler."""
    from chaoscypher_core.operations.extraction.schemas import (
        validate_raw_entities,
        validate_raw_relationships,
    )
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        aggregate_chunk_results,
    )

    try:
        adapter.start_extraction_job(job_id)
        adapter.update_step_progress(source_id, 1, 1, "Finalizing extraction results")

        job_record = adapter.get_extraction_job(job_id)
        detected_domain = job_record.get("detected_domain") if job_record else None
        forced_domain = job_record.get("forced_domain") if job_record else None

        # Guard against race: the atomic job counter (completed_chunks) can
        # reach terminal while individual ChunkExtractionTask rows are still
        # persisting their status update. Refuse to aggregate partial results.
        # When all tasks are terminal (or there are no tasks at all — zero-chunk
        # document), the guard passes and finalization proceeds normally.
        all_tasks = adapter.get_chunk_tasks_by_job(job_id)
        non_terminal = [t for t in all_tasks if t.get("status") not in TERMINAL_TASK_STATES]
        if non_terminal:
            logger.warning(
                "finalize_waiting_for_tasks",
                job_id=job_id,
                source_id=source_id,
                non_terminal_count=len(non_terminal),
                non_terminal_task_ids=[t.get("id") for t in non_terminal[:5]],
            )
            return {"status": "not_ready", "retry": True}

        completed_tasks = adapter.get_completed_chunk_results(job_id)

        # Phase 7 audit-remediation (2026-05-09): surface "X of Y chunks succeeded"
        # directly on the source row so operators don't have to subtract failure
        # counters from total_chunks.  This is a stat write (like total_chunks),
        # not a QualityCounter — set via update_source_columns.
        try:
            adapter.update_source_columns(
                source_id=source_id,
                database_name=database_name,
                updates={"chunks_completed_count": len(completed_tasks)},
            )
        except Exception:
            logger.warning(
                "chunks_completed_count_write_failed",
                source_id=source_id,
                chunks_completed=len(completed_tasks),
                exc_info=True,
            )

        if not completed_tasks:
            # No completed chunks — disambiguate "legitimately empty" from
            # "every chunk failed". If any failed tasks exist on the job,
            # route to fail_extraction so the source surfaces as status=error
            # with an aggregated chunk-error message. Otherwise (no tasks at
            # all, e.g. a math-only doc the chunker yielded nothing for),
            # keep the existing empty-commit path.
            #
            # NOTE: We derive failed tasks from all_tasks (already loaded
            # above) rather than calling adapter.get_failed_chunk_tasks(job_id),
            # because the latter filters to retryable tasks (retry_count <
            # max_retries) and would return empty for exhausted-retry chunks —
            # precisely the incident scenario (Ollama 404, all retries spent).
            failed_tasks = [t for t in all_tasks if t.get("status") == "failed"]

            if failed_tasks:
                # Aggregate the top-3 distinct error_messages so the operator
                # gets an actionable hint without dumping 100+ stack traces
                # into the row. The full per-chunk detail stays on the
                # ChunkExtractionTask rows for the Processing tab.
                top_errors = _aggregate_chunk_errors(failed_tasks, limit=3)
                summary_msg = (
                    f"Extraction failed: {len(failed_tasks)} of "
                    f"{len(failed_tasks) + len(completed_tasks)} chunks failed. "
                    f"Top errors: {top_errors}"
                )
                logger.error(
                    "finalize_extraction_all_chunks_failed",
                    job_id=job_id,
                    source_id=source_id,
                    failed_count=len(failed_tasks),
                    summary=summary_msg,
                )

                # Write LLM summary FIRST so failed-call counters land on
                # the row before _apply_failure clears the job reference.
                llm_summary = adapter.compute_llm_summary(
                    source_id,
                    database_name,
                    custom_input_cost=settings.llm.token_cost_input_per_million,
                    custom_output_cost=settings.llm.token_cost_output_per_million,
                )
                if llm_summary:
                    adapter.update_source_columns(
                        source_id=source_id,
                        database_name=database_name,
                        updates=llm_summary,
                    )

                # Offloaded to a worker thread so SafeSession._retry_delay
                # ``time.sleep`` calls during SQLITE_BUSY contention do not
                # block other in-flight handlers on the event loop
                # (2026-05-23 perf fix).
                def _run_fail_extraction_txn() -> None:
                    """Mark the extraction failed and complete its job in one transaction."""
                    with adapter.transaction():
                        adapter.fail_extraction(source_id, summary_msg)
                        adapter.complete_extraction_job(job_id)
                        adapter.update_step_progress(source_id, 0, 0, "")

                await asyncio.to_thread(_run_fail_extraction_txn)

                await trigger_next_waiting_extraction(adapter, database_name, settings)
                return {"status": "extraction_failed", "job_id": job_id}

            # Truly empty — pure-prose / math-heavy / zero-chunk document.
            # commit() will route to _commit_empty which promotes chunks and marks
            # the source committed with 0 nodes/edges. This handles documents that
            # are pure prose, math-heavy, or otherwise yield no extractable entities.
            logger.info(
                "finalize_extraction_empty_result",
                job_id=job_id,
                source_id=source_id,
                message="Extraction produced zero entities -- committing empty",
            )
            # Persist the extracted-state DB writes atomically. The queue
            # enqueue is hoisted OUTSIDE this block so the SQLite writer
            # lock is not held across the Valkey roundtrip (2026-05-20
            # writer-lock-contention root fix). If the enqueue fails after
            # the DB commit succeeds, the source sits at status='extracted'
            # with no commit task — ``SourceRecovery._classify_extracted``
            # (services/sources/recovery.py:840) detects exactly this case
            # via ``_queue_has_task_for`` and auto-dispatches a commit task
            # at the next reconcile pass.
            # Offloaded to a worker thread so SafeSession._retry_delay
            # ``time.sleep`` calls during SQLITE_BUSY contention do not
            # block other in-flight handlers on the event loop
            # (2026-05-23 perf fix).
            from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
                resolve_domain_fingerprint,
            )

            _dom_version, _dom_hash = resolve_domain_fingerprint(
                forced_domain or detected_domain, database_name
            )

            def _run_empty_extraction_complete_txn() -> None:
                """Complete an empty-result extraction in a single transaction."""
                with adapter.transaction():
                    adapter.complete_extraction(
                        source_id=source_id,
                        entities=[],
                        relationships=[],
                        forced_domain=forced_domain,
                        detected_domain=detected_domain,
                        domain_version=_dom_version,
                        domain_content_hash=_dom_hash,
                    )
                    adapter.complete_extraction_job(job_id)
                    adapter.update_step_progress(source_id, 0, 0, "")

            await asyncio.to_thread(_run_empty_extraction_complete_txn)

            await _queue_commit_phase(
                adapter,
                source_id,
                database_name,
                [],
                [],
                {
                    "entities": [],
                    "relationships": [],
                    "suggested_templates": [],
                    "suggested_edge_templates": [],
                    "inverse_relationships": {},
                },
                settings,
                chunk_sentences=None,
            )

            await trigger_next_waiting_extraction(adapter, database_name, settings)

            return {"status": "committed_empty", "job_id": job_id}

        # F47: schema-validate every persisted chunk result before aggregation.
        # If a row's raw_entities or raw_relationships drifted from the canonical
        # Pydantic shape (legacy data, hand-edited DB, or a writer-side bug that
        # slipped past on-write validation), raise DataIntegrityError so the
        # source goes to ERROR with a clear message rather than crashing later
        # during dedup/commit with an opaque KeyError or AttributeError.
        for task in completed_tasks:
            task_id = task.get("id")
            validate_raw_entities(
                task.get("raw_entities"),
                chunk_task_id=task_id,
                stage="read",
                logger=logger,
            )
            validate_raw_relationships(
                task.get("raw_relationships"),
                chunk_task_id=task_id,
                stage="read",
                logger=logger,
            )

        # Step 1: Aggregate chunk results (shared Core logic)
        aggregated = aggregate_chunk_results(completed_tasks)

        logger.info(
            "finalize_aggregation_complete",
            job_id=job_id,
            total_raw_entities=len(aggregated["entities"]),
            total_raw_relationships=len(aggregated["relationships"]),
            completed_chunks=len(completed_tasks),
        )

        # Workstream 2 (2026-05-08): surface aggregator-dropped relationships
        # as a row-level quality counter.  aggregate_chunk_results silently
        # drops malformed-index relationships as a defensive guard against bad
        # LLM output (out-of-bounds or bool source/target).  Drops are counted
        # in the return dict so we can surface them without adding adapter
        # access to the pure orchestration function.
        agg_dropped = aggregated.get("dropped_relationships_invalid_index", 0)
        if agg_dropped:
            from chaoscypher_core.services.quality.counters import (
                QualityCounter,
                increment_quality_counter,
            )

            await increment_quality_counter(
                adapter=adapter,
                source_id=source_id,
                database_name=database_name,
                counter=QualityCounter.AGGREGATOR_RELATIONSHIPS_DROPPED,
                n=agg_dropped,
            )

        # Step 1b: Resolve chunk sentences
        chunk_sentences = _resolve_chunk_sentences(aggregated)

        # Step 2: Deduplication (code-only, no LLM calls during finalization)
        from chaoscypher_core.services.quality.counters import (
            QualityCounter,
            increment_quality_counter,
        )
        from chaoscypher_core.services.sources.engine.extraction.extractor import (
            run_deduplication,
        )
        from chaoscypher_core.services.sources.engine.extraction.service import (
            ExtractionService,
        )
        from chaoscypher_core.services.sources.engine.extraction.utils.post_extraction import (
            apply_domain_type_aliases,
            apply_structural_and_normalization,
        )

        effective_domain = forced_domain or detected_domain
        from chaoscypher_core.repo_factories import get_embedding_service

        embedding_service = get_embedding_service()

        extraction_service = ExtractionService(
            graph_repository=graph_repository,
            llm_provider=llm_service,
            settings=engine_settings,
            embedding_service=embedding_service,
        )

        # Resolve the FilteringConfig once up-front so dedup, cross-chunk
        # filters, and the structural step all share the same slider-driven
        # values (esp. ``semantic_dedup_threshold``).
        pre_dedup_filtering_config = _resolve_finalizer_filtering_config(
            engine_settings=engine_settings,
            job_record=job_record,
        )

        # Backfill any chunk rows missing raw_entity_embeddings (in-flight rows
        # that pre-date the schema change). Steady state this is a no-op because
        # the chunk handler writes embeddings eagerly alongside raw_entities.
        # Returns None if backfill couldn't complete; dedup falls back to its
        # own batch_embed call in that case.
        precomputed_embeddings = await _ensure_chunk_embeddings(
            adapter=adapter,
            completed_tasks=completed_tasks,
            embedding_service=embedding_service,
            job_id=job_id,
        )

        # Step 1c: Apply domain type_aliases BEFORE dedup so name variants
        # split across alias types (``Historical Figure: Pierre`` +
        # ``Character: Pierre``) merge into one canonical entity. The
        # original type is preserved as ``properties.entity_subtype`` so
        # the refinement signal isn't lost. Resolve the domain once here
        # and reuse it further down for the structural-and-normalization
        # step (currently re-resolved at line ~633; preserves the existing
        # cross-file ordering rather than reordering it now).
        resolved_domain_obj_pre_dedup = extraction_service._resolve_domain(effective_domain)  # noqa: SLF001 - finalizer composes the same domain resolution path as extraction_service
        apply_domain_type_aliases(aggregated["entities"], resolved_domain_obj_pre_dedup)

        (
            deduplicated,
            remapped,
            cached_embeddings,
            dedup_filtering_log,
        ) = await run_deduplication(
            entities=aggregated["entities"],
            relationships=aggregated["relationships"],
            detected_domain=effective_domain,
            settings=engine_settings,
            embedding_service=embedding_service,
            domain_resolver=extraction_service,
            filtering_config=pre_dedup_filtering_config,
            adapter=adapter,
            source_id=source_id,
            database_name=database_name,
            precomputed_embeddings=precomputed_embeddings,
        )

        logger.info(
            "finalize_dedup_complete",
            job_id=job_id,
            entities_before=len(aggregated["entities"]),
            entities_after=len(deduplicated),
            relationships_after=len(remapped),
        )

        # Workstream 2 (2026-05-08): surface the merged-entity count as a
        # row-level quality counter. ``run_deduplication`` returns the
        # filtering log as a serialized dict; sum ``removed_count`` across
        # the dedup stages (exact + semantic) since both represent merges
        # into surviving entities. Best-effort — never block finalization.
        dedup_merged_count = sum(
            int(s.get("removed_count", 0) or 0)
            for s in dedup_filtering_log.get("stages", [])
            if s.get("stage") in {"exact_entity_dedup", "semantic_entity_dedup"}
        )
        if dedup_merged_count > 0:
            await increment_quality_counter(
                adapter=adapter,
                source_id=source_id,
                database_name=database_name,
                counter=QualityCounter.DEDUP_ENTITIES_MERGED,
                n=dedup_merged_count,
            )

        # Step 2b: Cross-chunk relationship filtering (Phase 6 reorder).
        # Type-constraint validation and relationship-limit enforcement
        # run AFTER dedup so canonical entities carry their consolidated
        # edges through the filter -- not chunk-local fragments. See
        # ``apply_cross_chunk_relationship_filters`` in extractor.py.
        # Returns the resolved FilteringConfig so the next step can
        # gate the structural filter on ``enable_structural_filter``
        # without re-resolving from the job record.
        (
            deduplicated,
            remapped,
            dedup_filtering_log,
            resolved_filtering_config,
        ) = _apply_post_dedup_filters(
            entities=deduplicated,
            relationships=remapped,
            engine_settings=engine_settings,
            job_record=job_record,
            existing_filtering_log=dedup_filtering_log,
            filtering_config=pre_dedup_filtering_config,
        )

        logger.info(
            "finalize_cross_chunk_filters_complete",
            job_id=job_id,
            relationships_after=len(remapped),
        )

        # Workstream 2 (2026-05-08): surface relationship drops from the
        # cross-chunk filters as row-level counters. Counters:
        #   * INVALID        — index validation + dedup (malformed indices)
        #   * TYPE_UNMATCHED — type-constraint drops (strict mode only)
        #   * CAPPED         — degree / total-count limit enforcement
        #   * DIRECTION_CORRECTED — source<->target swaps (always; not a drop)
        # ``relationship_type_constraint`` is carved out of INVALID into its own
        # counter so operators can distinguish schema violations from type mismatches.
        # ``relationship_direction_corrected`` uses removed_count as a carry field
        # (not a removal — the stage name disambiguates).
        # Read straight from the merged filtering-log dict so the wiring
        # stays single-sourced with what the UI's "Filtering" tab shows.
        if dedup_filtering_log:
            stages = dedup_filtering_log.get("stages", []) or []
            # INVALID: malformed-index drops + dedup — does NOT include type-constraint
            invalid_stages = {
                "relationship_index_validation",
                "relationship_dedup",
            }
            invalid_count = sum(
                int(s.get("removed_count", 0) or 0)
                for s in stages
                if s.get("stage") in invalid_stages
            )
            type_unmatched_count = sum(
                int(s.get("removed_count", 0) or 0)
                for s in stages
                if s.get("stage") == "relationship_type_constraint"
            )
            capped_count = sum(
                int(s.get("removed_count", 0) or 0)
                for s in stages
                if s.get("stage") == "relationship_limit_enforcement"
            )
            direction_corrected_count = sum(
                int(s.get("removed_count", 0) or 0)
                for s in stages
                if s.get("stage") == "relationship_direction_corrected"
            )
            # _fuzzy_type_match audit (2026-05-20): rescue counters. Each
            # stage carries the rescue count as its ``removed_count`` field
            # — not a removal, the stage name disambiguates (same pattern
            # as relationship_direction_corrected above).
            fuzzy_matched_count = sum(
                int(s.get("removed_count", 0) or 0)
                for s in stages
                if s.get("stage") == "relationship_type_fuzzy_matched"
            )
            fell_through_count = sum(
                int(s.get("removed_count", 0) or 0)
                for s in stages
                if s.get("stage") == "relationship_type_fell_through"
            )
            if invalid_count > 0:
                await increment_quality_counter(
                    adapter=adapter,
                    source_id=source_id,
                    database_name=database_name,
                    counter=QualityCounter.RELATIONSHIPS_DROPPED_INVALID,
                    n=invalid_count,
                )
            if type_unmatched_count > 0:
                await increment_quality_counter(
                    adapter=adapter,
                    source_id=source_id,
                    database_name=database_name,
                    counter=QualityCounter.RELATIONSHIPS_DROPPED_TYPE_UNMATCHED,
                    n=type_unmatched_count,
                )
            if capped_count > 0:
                await increment_quality_counter(
                    adapter=adapter,
                    source_id=source_id,
                    database_name=database_name,
                    counter=QualityCounter.RELATIONSHIPS_DROPPED_CAPPED,
                    n=capped_count,
                )
            if direction_corrected_count > 0:
                await increment_quality_counter(
                    adapter=adapter,
                    source_id=source_id,
                    database_name=database_name,
                    counter=QualityCounter.RELATIONSHIPS_DIRECTION_CORRECTED,
                    n=direction_corrected_count,
                )
            if fuzzy_matched_count > 0:
                await increment_quality_counter(
                    adapter=adapter,
                    source_id=source_id,
                    database_name=database_name,
                    counter=QualityCounter.RELATIONSHIPS_TYPE_FUZZY_MATCHED,
                    n=fuzzy_matched_count,
                )
            if fell_through_count > 0:
                await increment_quality_counter(
                    adapter=adapter,
                    source_id=source_id,
                    database_name=database_name,
                    counter=QualityCounter.RELATIONSHIPS_TYPE_FELL_THROUGH,
                    n=fell_through_count,
                )

            # Surface stages that wiped out a majority of their input as a
            # structured warning. Filed 2026-05-20 after a single source
            # dropped 39/40 relationships at relationship_type_constraint —
            # the source still committed cleanly with quality_grade="Excellent",
            # and the silent drop was only caught by manual graph inspection.
            # Threshold: drop ≥50% AND input ≥10 (small inputs skew rate
            # math — 1/2 dropped is not the same kind of signal as 39/40).
            # Operators grep for this event in neuron logs to flag domain
            # config / LLM-prompt regressions before they accumulate.
            for stage in stages:
                stage_name = stage.get("stage")
                input_n = int(stage.get("input_count", 0) or 0)
                removed_n = int(stage.get("removed_count", 0) or 0)
                if input_n < 10 or removed_n == 0:
                    continue
                drop_rate = removed_n / input_n
                if drop_rate >= 0.5:
                    logger.warning(
                        "extraction_high_drop_rate",
                        stage=stage_name,
                        input_count=input_n,
                        removed_count=removed_n,
                        drop_rate=round(drop_rate, 3),
                        source_id=source_id,
                        job_id=job_id,
                    )

        # Step 2c: Filter structural entities + apply domain-specific
        # type normalization, in lockstep with the service / standalone
        # paths via the shared helper. The structural filter strips
        # chapter / section / part markers (gated on the resolved
        # FilteringConfig so ``minimal`` / ``unfiltered`` modes skip it
        # rather than always stripping); type normalization re-types
        # generic ``Item``/``Concept``/``Unknown`` entities to their
        # domain target (e.g. ``Class`` for ``technical``) when the
        # description matches a domain rule. Production parity with
        # ``extract_entities_from_groups`` (Workstream 3, Tasks
        # 3.1+3.2). The structural-filtered counter still increments
        # here so quality dashboards reflect the worker pipeline.
        resolved_domain_obj = extraction_service._resolve_domain(effective_domain)  # noqa: SLF001 - finalizer composes the same domain resolution path as extraction_service
        normalization_rules = extraction_service.get_domain_normalization_rules(effective_domain)
        deduplicated, remapped, structural_filtered = apply_structural_and_normalization(
            deduplicated,
            remapped,
            domain=resolved_domain_obj,
            filtering_config=resolved_filtering_config,
            normalization_rules=normalization_rules,
        )
        if structural_filtered > 0:
            await increment_quality_counter(
                adapter=adapter,
                source_id=source_id,
                database_name=database_name,
                counter=QualityCounter.STRUCTURAL_ENTITIES_FILTERED,
                n=structural_filtered,
            )
            logger.info(
                "structural_entities_filtered",
                job_id=job_id,
                source_id=source_id,
                removed=structural_filtered,
                remaining=len(deduplicated),
            )

        # Step 3: Proceed directly to completion
        return await _complete_finalization(
            adapter=adapter,
            graph_repository=graph_repository,
            llm_service=llm_service,
            settings=settings,
            engine_settings=engine_settings,
            job_id=job_id,
            source_id=source_id,
            database_name=database_name,
            generate_embeddings=generate_embeddings,
            detected_domain=detected_domain,
            forced_domain=forced_domain,
            entities=deduplicated,
            relationships=remapped,
            cached_embeddings=cached_embeddings,
            completed_chunks=len(completed_tasks),
            chunk_sentences=chunk_sentences,
            cross_chunk_filtering_log=dedup_filtering_log,
        )

    except asyncio.CancelledError:
        return await _handle_finalize_cancellation(
            adapter, data, job_id, source_id, database_name, settings
        )

    except Exception as exc:
        logger.exception(
            "finalize_extraction_failed",
            job_id=job_id,
            source_id=source_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

        try:
            adapter.fail_extraction_job(job_id, str(exc))
        except Exception as fail_exc:
            logger.warning(
                "fail_handler_raised",
                event_key="fail_handler_raised",
                source_id=source_id,
                job_id=job_id,
                original_exception_type=type(exc).__name__,
                original_exception_message=str(exc),
                fail_exception_type=type(fail_exc).__name__,
                fail_exception_message=str(fail_exc),
            )
            raise
        try:
            adapter.fail_extraction(source_id, str(exc))
        except Exception as fail_exc:
            logger.warning(
                "fail_handler_raised",
                event_key="fail_handler_raised",
                source_id=source_id,
                job_id=job_id,
                original_exception_type=type(exc).__name__,
                original_exception_message=str(exc),
                fail_exception_type=type(fail_exc).__name__,
                fail_exception_message=str(fail_exc),
            )
            raise

        try:
            await trigger_next_waiting_extraction(adapter, database_name, settings)
        except Exception:
            # Workstream 8 (2026-05-07) — the next-waiting dispatch is a
            # cleanup hook; if it fails the in-flight job is already
            # being marked failed. SourceRecovery's 60s reconciler picks
            # up any waiting source the next tick. Log so the moment of
            # failure is queryable instead of being swallowed.
            logger.warning(
                "next_waiting_extraction_dispatch_failed",
                source_id=source_id,
                job_id=job_id,
                database_name=database_name,
                exc_info=True,
            )

        raise

    # No finally/disconnect - singleton adapter is long-lived


# ------------------------------------------------------------------ #
#  Shared completion logic
# ------------------------------------------------------------------ #


async def _complete_finalization(
    *,
    adapter: SqliteAdapter,
    graph_repository: GraphRepository,
    llm_service: LLMService,
    settings: Settings,
    engine_settings: EngineSettings,
    job_id: str,
    source_id: str,
    database_name: str,
    generate_embeddings: bool,
    detected_domain: str | None,
    forced_domain: str | None,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    cached_embeddings: list[Any],
    completed_chunks: int,
    chunk_sentences: list[list[str]] | None = None,
    cross_chunk_filtering_log: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared completion steps: normalize, suggest templates, embed, commit.

    Performs normalization, template suggestions, embedding generation, quality scoring,
    and queues the commit phase.

    Args:
        adapter: Storage adapter.
        graph_repository: GraphRepository for graph operations.
        llm_service: LLMService for AI operations.
        settings: Cortex application settings.
        engine_settings: Core EngineSettings for core library calls.
        job_id: Extraction job ID.
        source_id: Source file ID.
        database_name: Database context.
        generate_embeddings: Whether to generate entity embeddings.
        detected_domain: Auto-detected domain name.
        forced_domain: User-forced domain override.
        entities: Final deduplicated entities.
        relationships: Final relationships.
        cached_embeddings: Cached embeddings from semantic dedup.
        completed_chunks: Number of completed chunk tasks.
        chunk_sentences: Per-chunk sentence lists for evidence-based citations.
        cross_chunk_filtering_log: Cross-chunk pipeline filtering diagnostics.

    Returns:
        Result dictionary with final extraction statistics.

    """
    from chaoscypher_core.repo_factories import get_embedding_service
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        cache_quality_scores,
    )
    from chaoscypher_core.services.sources.engine.extraction.service import (
        ExtractionService,
    )

    extraction_service = ExtractionService(
        graph_repository=graph_repository,
        llm_provider=llm_service,
        settings=engine_settings,
        embedding_service=get_embedding_service(),
    )

    # Build extraction results (normalize, suggest templates, generate embeddings)
    extraction_results = await extraction_service.build_extraction_results(
        entities,
        relationships,
        generate_embeddings=generate_embeddings,
        cached_embeddings=cached_embeddings,
        detected_domain=detected_domain,
        forced_domain=forced_domain,
        extraction_depth="distributed",
    )

    extraction_results["metadata"]["chunks_processed"] = completed_chunks
    if cross_chunk_filtering_log:
        extraction_results["metadata"]["filtering_log"] = cross_chunk_filtering_log

    matched_entities = extraction_results["entities"]
    remapped_relationships = extraction_results["relationships"]
    matched_templates = extraction_results["matched_templates"]

    # Store embeddings. The helper owns its own ``adapter.transaction()``
    # block, so offload it to a worker thread — SafeSession._retry_delay
    # ``time.sleep`` calls during SQLITE_BUSY contention must not block
    # other in-flight handlers on the event loop (2026-05-23 perf fix).
    await asyncio.to_thread(
        _store_entity_embeddings,
        adapter,
        extraction_results,
        matched_entities,
        source_id,
        database_name,
    )

    # Compute and store LLM metrics summary. Always written — even when
    # llm_total_calls is 0 — so the failure path (all chunks failed before
    # writing metrics) doesn't leave the source row with stale defaults.
    # Pre-2026-05-21 this was gated on `llm_total_calls > 0`, which silently
    # hid the all-chunks-failed case behind committed_empty.
    llm_summary = adapter.compute_llm_summary(
        source_id,
        database_name,
        custom_input_cost=settings.llm.token_cost_input_per_million,
        custom_output_cost=settings.llm.token_cost_output_per_million,
    )
    if llm_summary:
        adapter.update_source_columns(
            source_id=source_id,
            database_name=database_name,
            updates=llm_summary,
        )
        logger.info(
            "llm_metrics_summary_stored",
            source_id=source_id,
            total_calls=llm_summary.get("llm_total_calls"),
            successful_calls=llm_summary.get("llm_successful_calls"),
            failed_calls=llm_summary.get("llm_failed_calls"),
            retry_calls=llm_summary.get("llm_retry_calls"),
            wasted_tokens=llm_summary.get("llm_wasted_tokens"),
            estimated_cost_usd=llm_summary.get("llm_estimated_cost_usd"),
        )

    # Get chunk_count for coverage score
    source_data = adapter.get_file(source_id, database_name)
    source_chunk_count = (source_data.get("chunk_count", 0) or 0) if source_data else 0

    # Cache quality scores
    cache_quality_scores(
        adapter=adapter,
        source_id=source_id,
        entities=matched_entities,
        relationships=remapped_relationships,
        domain_name=forced_domain or detected_domain,
        database_name=database_name,
        chunk_count=source_chunk_count,
    )

    # Persist the extracted-state DB writes atomically. The queue enqueue
    # is hoisted OUTSIDE this block so the SQLite writer lock is not held
    # across the Valkey roundtrip (2026-05-20 writer-lock-contention root
    # fix). If the enqueue fails after the DB commit succeeds, the source
    # sits at status='extracted' with no commit task —
    # ``SourceRecovery._classify_extracted`` (services/sources/recovery.py:840)
    # detects exactly this case via ``_queue_has_task_for`` and auto-
    # dispatches a commit task at the next reconcile pass.
    extraction_results.pop("embeddings", None)
    # Pluck the cross-chunk filtering log out of the results dict — it
    # used to ride along inside ``extraction_results.metadata`` but now
    # has its own ``sources.cross_chunk_filtering_log`` column so the
    # "Filtering" UI tab keeps working without re-loading every entity.
    filtering_log_metadata: dict[str, Any] | None = None
    metadata_block = extraction_results.get("metadata")
    if isinstance(metadata_block, dict):
        filtering_log_metadata = metadata_block.get("filtering_log")

    from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
        resolve_domain_fingerprint,
    )

    _dom_version, _dom_hash = resolve_domain_fingerprint(
        forced_domain or detected_domain, database_name
    )

    # Offloaded to a worker thread so SafeSession._retry_delay
    # ``time.sleep`` calls during SQLITE_BUSY contention do not block
    # other in-flight handlers on the event loop (2026-05-23 perf fix).
    def _run_complete_finalize_txn() -> None:
        """Run the full finalize-completion writes in one transaction."""
        with adapter.transaction():
            adapter.complete_extraction(
                source_id,
                entities=matched_entities,
                relationships=remapped_relationships,
                detected_domain=detected_domain,
                forced_domain=forced_domain,
                cross_chunk_filtering_log=filtering_log_metadata,
                domain_version=_dom_version,
                domain_content_hash=_dom_hash,
            )
            adapter.complete_extraction_job(job_id)
            adapter.update_step_progress(source_id, 0, 0, "")

    await asyncio.to_thread(_run_complete_finalize_txn)

    await _queue_commit_phase(
        adapter,
        source_id,
        database_name,
        matched_entities,
        remapped_relationships,
        extraction_results,
        settings,
        chunk_sentences=chunk_sentences,
    )

    logger.info(
        "finalize_extraction_completed",
        job_id=job_id,
        source_id=source_id,
        total_entities=len(matched_entities),
        total_relationships=len(remapped_relationships),
        templates_matched=len(matched_templates),
        detected_domain=detected_domain,
        forced_domain=forced_domain,
    )

    _complete_fname = source_data.get("filename", "unknown") if source_data else "unknown"
    event_bus.emit(
        "task_completed",
        action=f"Extraction complete: {_complete_fname}",
        source="worker",
        details={
            "source_id": source_id,
            "filename": _complete_fname,
            "entities": len(matched_entities),
            "relationships": len(remapped_relationships),
        },
        database_name=database_name,
    )

    await trigger_next_waiting_extraction(adapter, database_name, settings)

    return {
        "success": True,
        "job_id": job_id,
        "total_entities": len(matched_entities),
        "total_relationships": len(remapped_relationships),
        "templates_matched": len(matched_templates),
        "chunks_processed": completed_chunks,
    }


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #


def _resolve_finalizer_filtering_config(
    *,
    engine_settings: EngineSettings,
    job_record: dict[str, Any] | None,
) -> Any:
    """Resolve the FilteringConfig the finalizer should use end-to-end.

    The same resolution previously lived inline inside
    ``_apply_post_dedup_filters``. Extracting it lets the dedup step
    consume the slider-driven ``semantic_dedup_threshold`` without
    re-parsing the extraction-config JSON.

    Args:
        engine_settings: Engine settings (carries default filtering mode).
        job_record: Persisted job dict (carries extraction_config).

    Returns:
        Resolved FilteringConfig instance.
    """
    import json as _json

    from chaoscypher_core.exceptions import DataIntegrityError
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        resolve_filtering_config,
    )

    extraction_config: dict[str, Any] = {}
    if job_record:
        raw = job_record.get("extraction_config")
        if raw:
            try:
                extraction_config = _json.loads(raw) if isinstance(raw, str) else raw
            except Exception as exc:
                # Phase 1 (2026-05-08): pre-2026-05-08 we silently fell back
                # to {} and lost the user's filtering choice. Match the F47
                # schema-validation pattern further up this file: corrupt
                # persisted state is data-integrity, not recoverable.
                logger.exception(
                    "extraction_config_parse_failed",
                    job_id=job_record.get("id"),
                )
                msg = (
                    f"extraction_config column on job {job_record.get('id')} "
                    f"is not valid JSON; refusing to silently default. "
                    f"Re-extract the source to regenerate a valid extraction_config."
                )
                raise DataIntegrityError(msg, details={"job_id": job_record.get("id")}) from exc

    extraction_limits = extraction_config.get("extraction_limits") or {}
    # The preset selector lives at its own top-level key on the job's
    # extraction_config JSON — sibling to ``extraction_limits``, never
    # inlined. Old in-flight job rows that pre-date this split do not
    # exist (clean break, queue drained before deploy).
    filtering_mode = str(
        extraction_config.get("filtering_mode")
        or engine_settings.extraction.extraction_filtering_mode
    )
    return resolve_filtering_config(
        mode=filtering_mode,
        domain_overrides=dict(extraction_limits) if extraction_limits else None,
    )


def _apply_post_dedup_filters(
    *,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    engine_settings: EngineSettings,
    job_record: dict[str, Any] | None,
    existing_filtering_log: dict[str, Any] | None,
    filtering_config: Any | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
    Any,
]:
    """Apply cross-chunk relationship filters after dedup.

    Reads ``edge_type_constraints`` from the persisted job's
    ``extraction_config`` JSON column, then invokes
    ``apply_cross_chunk_relationship_filters`` with the supplied (or
    re-resolved) FilteringConfig. Merges the new filtering-log
    entries into the existing dedup log so the metadata trail stays
    single-rooted.

    Returns the resolved ``FilteringConfig`` alongside the filtered
    entities/relationships so the structural-filter step downstream
    can gate on ``enable_structural_filter`` without re-parsing the
    extraction config.

    Args:
        entities: Deduplicated entities.
        relationships: Remapped relationships.
        engine_settings: Engine settings (carries default filtering mode).
        job_record: Persisted job dict (carries extraction_config).
        existing_filtering_log: Dedup-stage filtering log (mutated by extension).
        filtering_config: Optional pre-resolved FilteringConfig. When
            omitted, the same resolution is reapplied here for
            backward compatibility with callers that haven't migrated
            to the up-front resolution path.

    Returns:
        Tuple of (entities, filtered_relationships, merged_filtering_log,
        filtering_config).
    """
    import json as _json

    from chaoscypher_core.exceptions import DataIntegrityError
    from chaoscypher_core.services.sources.engine.extraction.extractor import (
        apply_cross_chunk_relationship_filters,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )

    extraction_config: dict[str, Any] = {}
    if job_record:
        raw = job_record.get("extraction_config")
        if raw:
            try:
                extraction_config = _json.loads(raw) if isinstance(raw, str) else raw
            except Exception as exc:
                # Phase 1 (2026-05-08): pre-2026-05-08 we silently fell back
                # to {} and lost the user's filtering choice. Match the F47
                # schema-validation pattern further up this file: corrupt
                # persisted state is data-integrity, not recoverable.
                logger.exception(
                    "extraction_config_parse_failed",
                    job_id=job_record.get("id"),
                )
                msg = (
                    f"extraction_config column on job {job_record.get('id')} "
                    f"is not valid JSON; refusing to silently default. "
                    f"Re-extract the source to regenerate a valid extraction_config."
                )
                raise DataIntegrityError(msg, details={"job_id": job_record.get("id")}) from exc

    edge_type_constraints = extraction_config.get("edge_type_constraints") or None

    if filtering_config is None:
        filtering_config = _resolve_finalizer_filtering_config(
            engine_settings=engine_settings,
            job_record=job_record,
        )

    cross_filter_log = FilteringLog()
    entities, filtered_relationships = apply_cross_chunk_relationship_filters(
        entities=entities,
        relationships=relationships,
        edge_type_constraints=edge_type_constraints,
        filtering_config=filtering_config,
        filtering_log=cross_filter_log,
    )

    new_log_dict = cross_filter_log.to_dict()
    if existing_filtering_log is None:
        merged_log: dict[str, Any] | None = new_log_dict if new_log_dict.get("stages") else None
    else:
        merged_log = existing_filtering_log
        if new_log_dict and new_log_dict.get("stages"):
            merged_log.setdefault("stages", []).extend(new_log_dict["stages"])
            merged_log["total_removed"] = merged_log.get("total_removed", 0) + new_log_dict.get(
                "total_removed", 0
            )

    return entities, filtered_relationships, merged_log, filtering_config


def _resolve_chunk_sentences(
    aggregated: dict[str, Any],
) -> list[list[str]] | None:
    """Resolve pre-computed chunk sentences from aggregated results.

    Sentences are always populated upstream in ai_entities.py via
    split_into_sentences(chunk_content) and stored on each chunk task. This
    function just returns them if all chunks have them populated.

    Args:
        aggregated: Aggregated chunk results dict.

    Returns:
        List of per-chunk sentence lists, or None if any chunk is missing them.
    """
    raw_chunk_sentences: list[list[str] | None] = aggregated.get("chunk_sentences") or []
    if raw_chunk_sentences and all(s is not None for s in raw_chunk_sentences):
        return raw_chunk_sentences  # type: ignore[return-value]
    return None


async def _handle_finalize_cancellation(
    adapter: SqliteAdapter,
    data: dict[str, Any],
    job_id: str,
    source_id: str,
    database_name: str,
    settings: Settings,
    operation: str = OP_FINALIZE_EXTRACTION,
) -> dict[str, Any]:
    """Handle CancelledError during finalization (retry or fail permanently).

    Args:
        adapter: Storage adapter.
        data: Original task data (for retry count tracking).
        job_id: Extraction job ID.
        source_id: Source file ID.
        database_name: Database context.
        settings: Application settings.
        operation: Queue operation name for re-queuing.

    Returns:
        Result dictionary.
    """
    current_retries = data.get("finalize_retry_count", 0)

    if current_retries < settings.retries.extraction_finalize_max:
        logger.warning(
            "finalize_extraction_cancelled_requeuing",
            job_id=job_id,
            source_id=source_id,
            retry_count=current_retries + 1,
            max_retries=settings.retries.extraction_finalize_max,
            reason="worker_timeout_or_cancellation",
            operation=operation,
        )

        try:
            await queue_client.enqueue_task(
                queue=QUEUE_LLM,
                operation=operation,
                data={
                    "job_id": job_id,
                    "source_id": source_id,
                    "database_name": database_name,
                    "generate_embeddings": data.get("generate_embeddings", True),
                    "file_info": data.get("file_info", {}),
                    "finalize_retry_count": current_retries + 1,
                },
                priority=settings.priorities.background,
                metadata={
                    "job_id": job_id,
                    "source_id": source_id,
                    "operation_type": operation,
                },
            )
        except Exception:
            # Workstream 8 (2026-05-07) — surface re-enqueue failures on
            # the finalize-retry path. SourceRecovery rehydrates orphans
            # from the DB on its 60s loop; we just stop pretending the
            # moment of failure was invisible.
            logger.warning(
                "finalize_requeue_failed",
                job_id=job_id,
                source_id=source_id,
                database_name=database_name,
                retry_count=current_retries + 1,
                operation=operation,
                exc_info=True,
            )

        return {
            "success": False,
            "job_id": job_id,
            "error": "Finalization cancelled, requeued for retry",
            "retry_count": current_retries + 1,
        }

    # Max retries exceeded
    logger.warning(
        "finalize_extraction_cancelled_max_retries",
        job_id=job_id,
        source_id=source_id,
        retry_count=current_retries,
        max_retries=settings.retries.extraction_finalize_max,
        reason="max_retries_exceeded",
    )

    _cancel_job_msg = f"Finalization cancelled after {current_retries + 1} attempts (timeout)"
    _cancel_src_msg = (
        f"Extraction failed: finalization cancelled after {current_retries + 1} attempts"
    )
    try:
        adapter.fail_extraction_job(job_id, _cancel_job_msg)
    except Exception as fail_exc:
        logger.warning(
            "fail_handler_raised",
            event_key="fail_handler_raised",
            source_id=source_id,
            job_id=job_id,
            original_exception_type="asyncio.CancelledError",
            original_exception_message=_cancel_job_msg,
            fail_exception_type=type(fail_exc).__name__,
            fail_exception_message=str(fail_exc),
        )
        raise
    try:
        adapter.fail_extraction(source_id, _cancel_src_msg)
    except Exception as fail_exc:
        logger.warning(
            "fail_handler_raised",
            event_key="fail_handler_raised",
            source_id=source_id,
            job_id=job_id,
            original_exception_type="asyncio.CancelledError",
            original_exception_message=_cancel_src_msg,
            fail_exception_type=type(fail_exc).__name__,
            fail_exception_message=str(fail_exc),
        )
        raise

    try:
        await trigger_next_waiting_extraction(adapter, database_name, settings)
    except Exception:
        # Workstream 8 (2026-05-07) — same hook as the main path, just
        # firing from the cancellation-max-retries return. Logged so the
        # silent miss is queryable.
        logger.warning(
            "next_waiting_extraction_dispatch_failed",
            source_id=source_id,
            job_id=job_id,
            database_name=database_name,
            exc_info=True,
        )

    return {
        "success": False,
        "job_id": job_id,
        "error": f"Finalization cancelled after {current_retries + 1} attempts",
        "max_retries_exceeded": True,
    }


def _store_entity_embeddings(
    adapter: SqliteAdapter,
    extraction_results: dict[str, Any],
    matched_entities: list[dict[str, Any]],
    source_id: str,
    database_name: str,
) -> None:
    """Store entity embeddings in the database if generated.

    Uses the caller's ``SqliteAdapter`` (same adapter driving the
    commit phase) and wraps the repository write in
    ``adapter.transaction()`` so the write participates in the
    outermost unit-of-work or — if called outside a transaction —
    commits immediately.

    Args:
        adapter: Storage adapter with a live session.
        extraction_results: Full extraction results dict (contains ``embeddings`` key).
        matched_entities: Final entity list.
        source_id: Source file ID.
        database_name: Database context.
    """
    embeddings_result = extraction_results.get("embeddings")
    if not embeddings_result or not embeddings_result.get("embeddings"):
        return

    from chaoscypher_core.adapters.sqlite.repos import ExtractionRepository

    with adapter.transaction():
        session = adapter.session
        assert session is not None
        extraction_repo = ExtractionRepository(session, database_name)
        entity_metadata = [
            {
                "entity_index": idx,
                "entity_id": entity.get("id", f"entity_{idx}"),
            }
            for idx, entity in enumerate(matched_entities)
        ]
        extraction_repo.store_entity_embeddings(
            source_id=source_id,
            entity_metadata=entity_metadata,
            embeddings=embeddings_result["embeddings"],
            embedding_model=embeddings_result["model"],
            embedding_dimensions=embeddings_result["dimensions"],
        )


async def _queue_commit_phase(
    adapter: SqliteAdapter,
    source_id: str,
    database_name: str,
    matched_entities: list[dict[str, Any]],
    remapped_relationships: list[dict[str, Any]],
    extraction_results: dict[str, Any],
    settings: Settings,
    chunk_sentences: list[list[str]] | None = None,
) -> None:
    """Fetch complete file info and enqueue the commit operation.

    Args:
        adapter: Storage adapter.
        source_id: Source file ID.
        database_name: Database context.
        matched_entities: Final entity list.
        remapped_relationships: Final relationship list.
        extraction_results: Full extraction results dict.
        settings: Application settings.
        chunk_sentences: Per-chunk sentence lists for evidence-based citations.
    """
    complete_file_info = adapter.get_file(source_id, database_name)
    if not complete_file_info:
        logger.warning("commit_file_not_found", source_id=source_id)
        return

    logger.info(
        "queueing_commit_phase",
        source_id=source_id,
        entities_count=len(matched_entities),
        relationships_count=len(remapped_relationships),
        auto_enable=settings.auto_enable,
    )

    from chaoscypher_core.operations.queue_utils import (
        queue_import_commit,
    )

    commit_data: dict[str, Any] = {
        "entities": matched_entities,
        "relationships": remapped_relationships,
        "suggested_templates": extraction_results.get("suggested_templates", []),
        "suggested_edge_templates": extraction_results.get("suggested_edge_templates", []),
        "inverse_relationships": extraction_results.get("inverse_relationships", {}),
        "create_templates": True,
        "auto_enable": settings.auto_enable,
    }
    if chunk_sentences:
        commit_data["chunk_sentences"] = chunk_sentences

    await queue_import_commit(
        file_id=source_id,
        commit_data=commit_data,
        file_info=complete_file_info,
        adapter=adapter,
        database_name=database_name,
        priority=settings.priorities.background,
    )


async def trigger_next_waiting_extraction(
    adapter: SqliteAdapter,
    database_name: str,
    settings: Settings,
) -> None:
    """Trigger extraction for the next waiting source.

    Called after extraction completes (success or failure) to process
    the next source in the queue.

    Args:
        adapter: SQLite adapter for database operations.
        database_name: Database context.
        settings: Application settings.
    """
    next_source = adapter.get_oldest_waiting_extraction(database_name)

    if not next_source:
        logger.debug(
            "no_waiting_extractions",
            database_name=database_name,
        )
        return

    file_info = next_source.get("extraction_pending_file_info", {})
    source_id = next_source["id"]

    logger.info(
        "triggering_next_waiting_extraction",
        source_id=source_id,
        database_name=database_name,
        queued_at=next_source.get("extraction_queued_at"),
    )

    from chaoscypher_core.operations.queue_utils import (
        queue_import_analysis,
    )

    await queue_import_analysis(
        file_id=source_id,
        file_info=file_info,
        analysis_depth=file_info.get("analysis_depth", "full"),
        database_name=database_name,
        generate_embeddings=file_info.get("generate_embeddings", True),
        priority=settings.priorities.background,
        extra_metadata={"triggered_by": "extraction_queue"},
    )

    logger.info(
        "next_extraction_queued",
        source_id=source_id,
        database_name=database_name,
    )
