# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chunk Extraction Service - chunk task management and LLM extraction calls.

Provides the main ``ChunkExtractionOperationsService`` class that handles:
- Queuing individual chunk extraction tasks on the LLM queue
- Executing single-chunk extraction via AIEntityExtractor
- Stale task detection, cancellation handling, and failure recovery
- Progress tracking and automatic finalization triggering
"""

from __future__ import annotations

import asyncio
import json as _json
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import (
    OP_EXTRACT_CHUNK,
    OP_FINALIZE_EXTRACTION,
    QUEUE_LLM,
)
from chaoscypher_core.operations.extraction.extraction_finalizer import (
    finalize_extraction_handler,
)
from chaoscypher_core.operations.extraction.extraction_metrics_service import (
    persist_chunk_metrics,
)
from chaoscypher_core.operations.extraction.schemas import (
    validate_raw_entities,
    validate_raw_relationships,
)
from chaoscypher_core.queue import queue_client
from chaoscypher_core.queue.handler_spec import HandlerSpec


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.app_config import Settings

from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    ExclusionRule,
)


logger = structlog.get_logger(__name__)

# The minimum-chars threshold for the empty-output retry guard has been
# lifted to ExtractionSettings.empty_output_retry_min_chars (Phase 3,
# CLAUDE.md "zero hardcoded config values"). The guard is applied inside
# ``_extract_chunk_handler`` by reading ``engine_settings.extraction``.


# Keys mirrored 1:1 from the snapshot to ``EngineSettings.extraction`` and
# ``EngineSettings.llm`` when present in the job's extraction_config.
_SNAPSHOT_TO_EXTRACTION_KEYS: tuple[str, ...] = (
    "loop_max_out_of_bounds",
    "loop_max_source_type_repeat",
    "loop_max_property_repeat",
    "loop_invalid_relationship_rate_warmup",
    "loop_invalid_relationship_rate_threshold",
)
_SNAPSHOT_TO_LLM_KEYS: tuple[str, ...] = (
    "extraction_examples_enabled",
    "extraction_examples_max_chars",
)


def _apply_snapshot_overrides(engine_settings: Any, snapshot: dict[str, Any]) -> Any:
    """Return a copy of ``engine_settings`` with snapshot overrides applied.

    Snapshot version 2 snapshots extraction-time LLM tuning +
    loop-detector thresholds at job creation. The chunk handler reads
    them here so a mid-job edit to ``settings.yaml`` cannot bleed into
    in-flight chunks. Older snapshots (``snapshot_version`` absent or
    <2) are missing the keys; each ``snapshot.get(key)`` simply
    returns ``None`` and we keep the live setting as the fallback.

    Args:
        engine_settings: The live ``EngineSettings`` instance.
        snapshot: Decoded ``extraction_config`` dict from the job row.

    Returns:
        A new settings instance — either the original (when nothing to
        override) or a ``model_copy`` with the patched sub-models.
    """
    extraction_updates: dict[str, Any] = {}
    for key in _SNAPSHOT_TO_EXTRACTION_KEYS:
        value = snapshot.get(key)
        if value is not None:
            extraction_updates[key] = value
    llm_updates: dict[str, Any] = {}
    for key in _SNAPSHOT_TO_LLM_KEYS:
        value = snapshot.get(key)
        if value is not None:
            llm_updates[key] = value

    if not extraction_updates and not llm_updates:
        return engine_settings

    updates: dict[str, Any] = {}
    if extraction_updates:
        updates["extraction"] = engine_settings.extraction.model_copy(update=extraction_updates)
    if llm_updates:
        updates["llm"] = engine_settings.llm.model_copy(update=llm_updates)
    return engine_settings.model_copy(update=updates)


def _load_exclusion_rules(raw: Any) -> list[ExclusionRule] | None:
    """Reconstitute ``ExclusionRule`` objects from queue-payload dicts.

    The import-side serializes rules via ``model_dump()`` so the
    extraction_config dict round-trips through JSON safely. On the
    consumer side we re-validate into typed objects before handing them
    to the extractor.
    """
    if not raw:
        return None
    return [ExclusionRule.model_validate(r) for r in raw]


class ChunkExtractionOperationsService:
    """Service for chunk-based extraction operations on LLM queue.

    Enables per-chunk visibility, parallel scaling, and individual retry
    for document extraction tasks.
    """

    def __init__(
        self,
        graph_repository: GraphRepository | None = None,
        config_manager: Any = None,
        llm_service: Any = None,
        source_repository: Any = None,
    ) -> None:
        """Initialize chunk extraction operations service.

        Args:
            graph_repository: GraphRepository for graph operations.
            config_manager: ConfigManager for settings.
            llm_service: LLMService for AI operations.
            source_repository: SqliteAdapter for storage.

        """
        self.graph_repository = graph_repository
        self.config_manager = config_manager
        self.llm_service = llm_service
        self.source_repository = source_repository

        # Both LLM-queue handlers are idempotent (extract_chunk has a DB
        # short-circuit, finalize_extraction has a status short-circuit) so
        # they opt into retry_on_crash=True.
        #
        # extract_chunk opts OUT of queue-level transient retries because
        # extract_chunk_handler owns its own retry counter (is_retryable +
        # retry_count, see chunk_extraction_handler.py) — letting the queue
        # also retry would multiply hosted LLM calls.
        #
        # finalize_extraction keeps queue-level transient retries ON
        # (default). It only has a domain retry counter for asyncio
        # cancellation; a transient ConnectionError / DB blip raised inline
        # during aggregation, embedding, dedup, or commit has no domain
        # retry budget and would otherwise fail terminally on the first
        # hiccup.
        self.operation_handlers = {
            OP_EXTRACT_CHUNK: HandlerSpec(
                handler=self._extract_chunk_handler,
                retry_on_crash=True,
                retry_on_transient=False,
            ),
            OP_FINALIZE_EXTRACTION: HandlerSpec(
                handler=self._finalize_extraction_handler,
                retry_on_crash=True,
            ),
        }

        logger.info("chunk_extraction_operations_service_initialized")

    def register_handlers(self) -> None:
        """Register extraction operation handlers with LLM queue."""
        queue_client.register_handlers(QUEUE_LLM, self.operation_handlers)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Queue methods
    # ------------------------------------------------------------------
    async def queue_extract_chunk(
        self,
        chunk_task_id: str,
        job_id: str,
        database_name: str,
        chunk_index: int,
        hierarchical_group_id: str | None = None,
        small_chunk_ids: list[str] | None = None,
        priority: int = 50,
    ) -> str:
        """Queue a single chunk for extraction.

        Template data (guidance, examples) is read from the job's
        ``extraction_config`` column by the handler, not passed per-chunk.

        Payload discipline: the chunk text is NOT
        carried in the queue payload. The handler rehydrates it at
        dispatch time by fetching ``small_chunk_ids`` from the DB via
        ``adapter.get_chunks_by_ids(...)``. A combined hierarchical
        group can be many KB — keeping that out of Valkey is a
        material memory win at document scale.

        Args:
            chunk_task_id: Unique task ID (stored in ChunkExtractionTask).
            job_id: Parent extraction job ID.
            database_name: Database context.
            chunk_index: Index of this chunk in the document.
            hierarchical_group_id: Reference to hierarchical chunk group.
            small_chunk_ids: IDs of small chunks in this group. The
                handler fetches chunk text for these IDs at dispatch
                time.
            priority: Task priority (0-100).

        Returns:
            Queue task ID.

        """
        return await queue_client.enqueue_task(
            queue=QUEUE_LLM,
            operation=OP_EXTRACT_CHUNK,
            data={
                "chunk_task_id": chunk_task_id,
                "job_id": job_id,
                "database_name": database_name,
                "chunk_index": chunk_index,
                "hierarchical_group_id": hierarchical_group_id,
                "small_chunk_ids": small_chunk_ids,
            },
            priority=priority,
            metadata={
                "job_id": job_id,
                "chunk_task_id": chunk_task_id,
                "chunk_index": chunk_index,
                "operation_type": OP_EXTRACT_CHUNK,
            },
        )

    async def queue_finalize_extraction(
        self,
        job_id: str,
        source_id: str,
        database_name: str,
        generate_embeddings: bool = True,
        file_info: dict[str, Any] | None = None,
        priority: int = 50,
    ) -> str:
        """Queue finalization after all chunks are extracted.

        Args:
            job_id: Extraction job ID.
            source_id: Source file being processed.
            database_name: Database context.
            generate_embeddings: Whether to generate entity embeddings.
            file_info: File information for template suggestions.
            priority: Task priority (0-100).

        Returns:
            Queue task ID.

        """
        return await queue_client.enqueue_task(
            queue=QUEUE_LLM,
            operation=OP_FINALIZE_EXTRACTION,
            data={
                "job_id": job_id,
                "source_id": source_id,
                "database_name": database_name,
                "generate_embeddings": generate_embeddings,
                "file_info": file_info or {},
            },
            priority=priority,
            metadata={
                "job_id": job_id,
                "source_id": source_id,
                "operation_type": OP_FINALIZE_EXTRACTION,
            },
        )

    # ------------------------------------------------------------------
    # Operation handlers
    # ------------------------------------------------------------------
    async def _extract_chunk_handler(  # noqa: PLR0911
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute single chunk extraction.

        Args:
            data: Task data with chunk content and identifiers.
            metadata: Task metadata.
            task_id: Queue task ID.

        Returns:
            Result dictionary with extracted entities/relationships.

        """
        import time

        from chaoscypher_core.analytics.llm_metrics import LLMMetricsCollector
        from chaoscypher_core.app_config import get_settings
        from chaoscypher_core.app_config.engine_factory import build_engine_settings
        from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
            AIEntityExtractor,
        )
        from chaoscypher_core.services.sources.engine.extraction.utils.text_preparation import (
            prepare_text_for_extraction,
        )

        chunk_task_id = data["chunk_task_id"]
        job_id = data["job_id"]
        database_name = data["database_name"]
        chunk_index = data["chunk_index"]
        small_chunk_ids = data.get("small_chunk_ids") or []

        settings = get_settings()
        engine_settings = build_engine_settings(settings)
        adapter = self.source_repository

        # Stale task detection
        skip_result = _check_stale_chunk_task(adapter, chunk_task_id, job_id, chunk_index)
        if skip_result:
            return skip_result

        # Rehydrate chunk text from the database (Phase 5 Task D —
        # queue payload carries only IDs). The fetch happens AFTER the
        # stale-task check so we do not pay the DB cost for tasks we
        # would skip anyway.
        if not small_chunk_ids:
            logger.warning(
                "extract_chunk_no_small_chunk_ids",
                chunk_task_id=chunk_task_id,
                job_id=job_id,
                chunk_index=chunk_index,
            )
            return {
                "success": False,
                "skipped": True,
                "reason": "no_small_chunk_ids",
                "chunk_task_id": chunk_task_id,
            }
        fetched_chunks = adapter.get_chunks_by_ids(small_chunk_ids, database_name)
        if len(fetched_chunks) < len(small_chunk_ids):
            logger.warning(
                "extract_chunk_missing_chunk_rows",
                chunk_task_id=chunk_task_id,
                job_id=job_id,
                chunk_index=chunk_index,
                requested=len(small_chunk_ids),
                found=len(fetched_chunks),
            )
            if not fetched_chunks:
                return {
                    "success": False,
                    "skipped": True,
                    "reason": "chunks_not_found",
                    "chunk_task_id": chunk_task_id,
                }
        chunk_content = prepare_text_for_extraction(
            "\n\n".join(c["content"] for c in fetched_chunks if c.get("content"))
        )

        job = adapter.get_extraction_job(job_id)
        if job is None:
            return {
                "success": False,
                "skipped": True,
                "reason": "job_not_found",
                "chunk_task_id": chunk_task_id,
            }
        if job.get("status") == "cancelled":
            return {
                "success": False,
                "skipped": True,
                "reason": "job_cancelled",
                "chunk_task_id": chunk_task_id,
            }
        source_id = job.get("source_id")

        # Pause guard: runs after the existing job-level short-circuits
        # but before the LLM call. A paused source returns cleanly
        # without touching start_chunk_task_with_input or the
        # AIEntityExtractor.
        if isinstance(source_id, str):
            from chaoscypher_core.operations.pause_guard import check_paused

            pause_check = check_paused(
                source_id=source_id,
                database_name=database_name,
                adapter=adapter,
            )
            if pause_check.paused:
                logger.info(
                    "handler_skipped_paused",
                    handler="_extract_chunk_handler",
                    source_id=source_id,
                    chunk_task_id=chunk_task_id,
                    scope=pause_check.scope,
                    reason=pause_check.reason,
                )
                return {"skipped": "paused"}

        # Read extraction config from job record (preferred), fall back to per-chunk data
        _extraction_config_raw = job.get("extraction_config")
        _extraction_config: dict[str, Any] = (
            _json.loads(_extraction_config_raw) if _extraction_config_raw else {}
        )
        entity_guidance = (
            _extraction_config.get("entity_guidance", data.get("entity_guidance")) or ""
        )
        relationship_guidance = (
            _extraction_config.get("relationship_guidance", data.get("relationship_guidance")) or ""
        )

        # Workstream 8 (2026-05-07) — read LLM tuning + loop thresholds
        # from the job's snapshot when present, falling back to live
        # settings for legacy jobs (snapshot_version<2 or absent). The
        # snapshot pins values at job-creation time so a mid-job edit
        # to settings.yaml does not drift across in-flight chunks.
        _temperature_override: float | None = _extraction_config.get("extraction_temperature")
        _max_tokens_override: int | None = _extraction_config.get("extraction_max_tokens")

        logger.info(
            "extract_chunk_started",
            chunk_task_id=chunk_task_id,
            job_id=job_id,
            chunk_index=chunk_index,
            content_length=len(chunk_content),
        )

        # Create LLM metrics collector
        provider = settings.llm.chat_provider
        extraction_model = getattr(settings.llm, f"{provider}_extraction_model", None)
        chat_model = getattr(settings.llm, f"{provider}_chat_model", "unknown")
        metrics_collector = LLMMetricsCollector(
            source_id=source_id,
            chunk_task_id=chunk_task_id,
            database_name=database_name,
            operation_type="entity_extraction",
            provider=provider,
            model=extraction_model or chat_model,
            custom_input_cost=settings.llm.token_cost_input_per_million,
            custom_output_cost=settings.llm.token_cost_output_per_million,
        )

        try:
            adapter.start_chunk_task_with_input(chunk_task_id, chunk_content)

            # Spend-cap pre-check (2026-05-19, P0): refuse extraction
            # before the LLM call if the per-source or per-day token
            # budget is already at/over the cap. Raises permanent
            # LLMSpendCapExceededError — queue won't retry, source is
            # marked failed instead of continuing to bill.
            from chaoscypher_core.services.llm.spend import get_llm_spend_tracker

            spend_tracker = get_llm_spend_tracker()
            spend_tracker.check_and_raise(
                source_id if isinstance(source_id, str) else None,
                settings,
                adapter=adapter,
                database_name=database_name,
            )

            # Workstream 8 (2026-05-07) — apply snapshot overrides for
            # the loop-detector thresholds + examples flags so a mid-job
            # settings.yaml edit cannot drift across in-flight chunks.
            # Each key falls back to the live engine_settings value when
            # the snapshot doesn't carry it (legacy job rows).
            engine_settings = _apply_snapshot_overrides(engine_settings, _extraction_config)

            extractor = AIEntityExtractor(settings=engine_settings)

            # Rate-limited stream-activity heartbeat. Touches last_activity_at
            # at most once every stream_heartbeat_min_interval_seconds while
            # the LLM stream is producing tokens. If the stream stalls (TCP
            # open but no tokens emitted) the callback simply stops firing
            # and SourceRecovery correctly classifies the source as stalled
            # after stalled_threshold_seconds — a wall-clock heartbeat would
            # mask this real failure mode.
            heartbeat_min_interval = float(
                settings.source_recovery.stream_heartbeat_min_interval_seconds
            )
            last_beat_at = 0.0

            def _on_stream_progress() -> None:
                """Stamp ``last_activity_at`` at most once per heartbeat interval.

                Silently no-ops when ``source_id`` is not a string (recovery
                tracking is source-scoped). DB write failures are logged but
                never raised — a heartbeat hiccup must not poison the chunk.
                """
                nonlocal last_beat_at
                if not isinstance(source_id, str):
                    return
                now = time.monotonic()
                if now - last_beat_at < heartbeat_min_interval:
                    return
                last_beat_at = now
                try:
                    adapter.update_source_last_activity(
                        source_id=source_id,
                        database_name=database_name,
                        at_time=datetime.now(UTC),
                    )
                except Exception as hb_exc:
                    logger.warning(
                        "stream_heartbeat_db_write_failed",
                        source_id=source_id,
                        chunk_task_id=chunk_task_id,
                        error_type=type(hb_exc).__name__,
                        error_message=str(hb_exc),
                    )

            llm_start = time.perf_counter()
            (
                chunk_entities,
                chunk_relationships,
                input_tokens,
                output_tokens,
                extraction_metrics,
            ) = await extractor.extract_single_chunk(
                chunk_content=chunk_content,
                node_templates_formatted=_extraction_config.get(
                    "node_templates_formatted", data.get("node_templates_formatted", "")
                ),
                edge_templates_formatted=_extraction_config.get(
                    "edge_templates_formatted", data.get("edge_templates_formatted", "")
                ),
                entity_guidance=entity_guidance or None,
                relationship_guidance=relationship_guidance or None,
                entity_examples=_extraction_config.get(
                    "entity_examples_formatted", data.get("entity_examples_formatted")
                )
                or None,
                relationship_examples=_extraction_config.get(
                    "relationship_examples_formatted",
                    data.get("relationship_examples_formatted"),
                )
                or None,
                metrics_collector=metrics_collector,
                domain_extraction_limits=_extraction_config.get("extraction_limits"),
                filtering_mode=_extraction_config.get("filtering_mode"),
                entity_exclusions=_load_exclusion_rules(
                    _extraction_config.get("entity_exclusions")
                ),
                strict_entity_types=bool(_extraction_config.get("strict_entity_types", False)),
                valid_entity_type_names=set(_extraction_config.get("valid_entity_type_names") or [])
                or None,
                evidence_validation_mode=_extraction_config.get("evidence_validation_mode"),
                # NOTE: ``edge_type_constraints`` from extraction_config is no
                # longer threaded into ``extract_single_chunk`` -- type-constraint
                # validation runs cross-chunk after dedup in the finalizer; see
                # ``apply_cross_chunk_relationship_filters`` (extractor.py).
                on_stream_progress=_on_stream_progress,
                temperature_override=_temperature_override,
                max_tokens_override=_max_tokens_override,
                adapter=adapter,
                source_id=source_id if isinstance(source_id, str) else None,
                database_name=database_name,
            )
            llm_duration_ms = int((time.perf_counter() - llm_start) * 1000)

            # Empty-output guard. The LLM occasionally emits zero
            # tokens despite consuming the full input budget — qwen3
            # reasoning-mode timeouts, gemini RECITATION soft-stops,
            # ollama transient stream resets — and the rest of the
            # pipeline cannot distinguish that from "this chunk
            # legitimately has nothing to extract". Treat empty output
            # on a non-trivial chunk as a retryable transient so the
            # existing retry path requeues; if max_retries is reached
            # the chunk lands in 'failed' and surfaces in the UI.
            if (
                output_tokens == 0
                and len(chunk_content) >= engine_settings.extraction.empty_output_retry_min_chars
            ):
                from chaoscypher_core.exceptions import LLMServiceError

                raise LLMServiceError(
                    provider=settings.llm.chat_provider,
                    model=extraction_model or chat_model,
                    reason=(
                        f"LLM produced empty extraction output for "
                        f"{len(chunk_content)}-char chunk after {llm_duration_ms}ms"
                    ),
                )

            # Eager-embed raw entities BEFORE the persistence transaction so
            # the embedding HTTP call never runs while the SQLite writer lock
            # is held (2026-05-20 writer-lock-contention root fix — the same
            # principle the commit-pipeline hoist enforces). A finalize-handler
            # crash before complete_chunk_task_with_output discards the
            # pre-computed embeddings; finalize backfills next pass. Best-
            # effort throughout: factory-load OR embed failure both fall
            # through to NULL persistence. Skips the factory lookup entirely
            # on empty chunks.
            _chunk_entity_embeddings: list[list[float]] | None = None
            if chunk_entities:
                try:
                    from chaoscypher_core.repo_factories import (
                        get_embedding_service,
                    )

                    _embedding_service = get_embedding_service()
                except Exception as exc:
                    logger.warning(
                        "chunk_entity_embedding_service_unavailable",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                    _embedding_service = None
                _chunk_entity_embeddings = await _compute_chunk_entity_embeddings(
                    chunk_entities, _embedding_service
                )

            # Persist metrics, prompts, and chunk output atomically. A mid-write
            # exception rolls back all three so the chunk-retry path replays
            # from a clean slate. Audit fix #H/core (complete_chunk transaction).
            #
            # The transaction body is offloaded to a worker thread via
            # ``asyncio.to_thread`` so any ``SafeSession._retry_delay``
            # ``time.sleep`` triggered by SQLITE_BUSY contention only blocks
            # this handler's thread, not the event loop running the other
            # Operations-queue slots (2026-05-23 perf fix; matches the
            # pattern of the 2026-05-21 ``loader_registry.load_document``
            # hoist into ``asyncio.to_thread``).
            def _run_chunk_persist_txn() -> tuple[str, bool, int]:
                """Persist this chunk's metrics inside one write transaction."""
                with adapter.transaction():
                    persist_chunk_metrics(
                        adapter,
                        metrics_collector,
                        chunk_task_id,
                        chunk_index,
                        chunk_content,
                        chunk_entities,
                        chunk_relationships,
                    )

                    # Add chunk metadata to entities and relationships
                    for entity in chunk_entities:
                        entity["chunk_index"] = chunk_index
                    for rel in chunk_relationships:
                        rel["chunk_index"] = chunk_index

                    # F47: schema-validate before persistence. A malformed entity or
                    # relationship dict here means the parser/handler upstream emitted
                    # something off-spec — surface it now (raises DataIntegrityError,
                    # rolling the transaction back so the chunk is marked failed and
                    # the corrupt payload never lands on the row) rather than letting
                    # it land in the JSON column and fail later during finalization.
                    validate_raw_entities(
                        chunk_entities,
                        chunk_task_id=chunk_task_id,
                        stage="write",
                        logger=logger,
                    )
                    validate_raw_relationships(
                        chunk_relationships,
                        chunk_task_id=chunk_task_id,
                        stage="write",
                        logger=logger,
                    )

                    # Store shared prompts on the job (once) — must happen before
                    # stripping _prompt_data from extraction_metrics
                    _store_job_prompts(adapter, job_id, extraction_metrics)

                    # Store output + results in single atomic close. Pop the
                    # Workstream 8 observability fields so they don't linger
                    # in the metrics dict after persistence (the chunk
                    # handler increments the source-level counters below).
                    _finish_reason = extraction_metrics.pop("finish_reason", "unknown")
                    _aborted_by_loop = bool(extraction_metrics.pop("aborted_by_loop", False))
                    # Workstream 2 (2026-05-08): pop the parser-drop count so
                    # it gets incremented on the source row below alongside
                    # the other LLM-stage observability counters.
                    _parser_lines_dropped = int(
                        extraction_metrics.pop("parser_lines_dropped", 0) or 0
                    )
                    adapter.complete_chunk_task_with_output(
                        task_id=chunk_task_id,
                        llm_response_json=extraction_metrics.pop("raw_llm_response", ""),
                        llm_duration_ms=llm_duration_ms,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        context_window_available=settings.llm.ai_context_window,
                        raw_entities=chunk_entities,
                        raw_entity_embeddings=_chunk_entity_embeddings,
                        raw_relationships=chunk_relationships,
                        invalid_relationship_count=extraction_metrics.get(
                            "invalid_relationship_count", 0
                        ),
                        chunk_sentences=extraction_metrics.get("sentences"),
                        filtering_log=extraction_metrics.pop("filtering_log", None),
                        finish_reason=_finish_reason,
                        aborted_by_loop=_aborted_by_loop,
                    )
                    return _finish_reason, _aborted_by_loop, _parser_lines_dropped

            (
                _chunk_finish_reason,
                _chunk_aborted_by_loop,
                _chunk_parser_lines_dropped,
            ) = await asyncio.to_thread(_run_chunk_persist_txn)

            # Track tokens AFTER successful SQLite commit so a rollback does not
            # leave a phantom Valkey bump that double-counts on retry.
            if input_tokens > 0 or output_tokens > 0:
                from chaoscypher_core.queue import queue_client as _qc

                await _qc.track_tokens(QUEUE_LLM, input_tokens, output_tokens)

                # Feed the spend tracker so the per-source / per-day cap
                # observes this chunk's consumption. Same post-commit
                # placement as the queue counter to avoid double-counting.
                spend_tracker.record(
                    source_id if isinstance(source_id, str) else None,
                    input_tokens + output_tokens,
                    adapter=adapter,
                    database_name=database_name,
                )

            # Bump source-level observability counters. Done after the
            # transaction so a rollback (which would have raised before
            # this point) cannot double-count. Both increments are
            # best-effort — ``increment_quality_counter`` already
            # logs+swallows any failure.
            if isinstance(source_id, str):
                from chaoscypher_core.services.quality.counters import (
                    QualityCounter,
                    increment_quality_counter,
                )

                if _chunk_finish_reason == "length":
                    await increment_quality_counter(
                        adapter=adapter,
                        source_id=source_id,
                        database_name=database_name,
                        counter=QualityCounter.LLM_CHUNKS_TRUNCATED,
                        n=1,
                    )
                if _chunk_aborted_by_loop:
                    await increment_quality_counter(
                        adapter=adapter,
                        source_id=source_id,
                        database_name=database_name,
                        counter=QualityCounter.LLM_CHUNKS_ABORTED_BY_LOOP,
                        n=1,
                    )
                if _chunk_parser_lines_dropped > 0:
                    await increment_quality_counter(
                        adapter=adapter,
                        source_id=source_id,
                        database_name=database_name,
                        counter=QualityCounter.PARSER_LINES_DROPPED,
                        n=_chunk_parser_lines_dropped,
                    )

            # Update progress and maybe trigger finalization
            await self._update_chunk_progress(
                adapter=adapter,
                job_id=job_id,
                source_id=source_id,
                database_name=database_name,
                chunk_task_id=chunk_task_id,
                chunk_index=chunk_index,
                task_outcome="completed",
                settings=settings,
            )

            # Checkpoint last_activity_at so the source-reconciler can
            # distinguish real progress from a stall. This runs on the
            # per-chunk hot path so the checkpoint is fine-grained.
            adapter.update_source_last_activity(
                source_id=source_id,
                database_name=database_name,
                at_time=datetime.now(UTC),
            )

            return {
                "success": True,
                "chunk_task_id": chunk_task_id,
                "entity_count": len(chunk_entities),
                "relationship_count": len(chunk_relationships),
            }

        except asyncio.CancelledError:
            return await self._handle_chunk_cancellation(
                adapter,
                data,
                chunk_task_id,
                job_id,
                database_name,
                chunk_index,
                settings,
            )

        except Exception as exc:
            return await self._handle_chunk_failure(
                adapter,
                exc,
                chunk_task_id,
                job_id,
                database_name,
                chunk_index,
                settings,
                data=data,
            )

    async def _finalize_extraction_handler(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Delegate to the extraction finalizer module.

        Args:
            data: Task data with job ID and configuration.
            metadata: Task metadata.
            task_id: Queue task ID.

        Returns:
            Result dictionary with final extraction statistics.

        """
        return await finalize_extraction_handler(
            graph_repository=self.graph_repository,
            llm_service=self.llm_service,
            source_repository=self.source_repository,
            chunk_extraction_service=self,
            data=data,
            metadata=metadata,
            task_id=task_id,
        )

    # ------------------------------------------------------------------
    # _extract_chunk_handler helpers
    # ------------------------------------------------------------------

    async def _update_chunk_progress(
        self,
        *,
        adapter: SqliteAdapter,
        job_id: str,
        source_id: str,
        database_name: str,
        chunk_task_id: str,
        chunk_index: int,
        task_outcome: str,
        settings: Settings,
    ) -> None:
        """Atomically bump the chunk counter and auto-enqueue finalize.

        Uses ``adapter.increment_job_completed_and_check`` — a
        transactional UPDATE-then-read — so two concurrent chunk
        handlers can't both observe the terminal transition. The
        single transaction that observes ``is_terminal=True`` is the
        one that enqueues OP_FINALIZE_EXTRACTION.

        Args:
            adapter: Storage adapter.
            job_id: Extraction job ID.
            source_id: Source under extraction (needed for step progress
                and finalize enqueue metadata).
            database_name: Database context.
            chunk_task_id: Chunk task ID for logging.
            chunk_index: Chunk index for logging.
            task_outcome: Either ``"completed"`` or ``"failed"``.
            settings: Application settings.
        """
        progress = adapter.increment_job_completed_and_check(
            job_id=job_id,
            database_name=database_name,
            outcome=task_outcome,
        )

        # Update step progress for the UI (counted = completed + failed
        # so the bar keeps advancing even when chunks fail)
        counted = progress["completed"] + progress["failed"]
        total = progress["total"]
        if source_id:
            adapter.update_step_progress(
                source_id,
                counted,
                total,
                f"Analyzing chunk {counted}/{total}",
            )

        logger.info(
            "chunk_progress_updated",
            chunk_task_id=chunk_task_id,
            job_id=job_id,
            chunk_index=chunk_index,
            outcome=task_outcome,
            completed=progress["completed"],
            failed=progress["failed"],
            total=total,
            is_terminal=progress["is_terminal"],
        )

        if progress["is_terminal"]:
            job = adapter.get_extraction_job(job_id)
            generate_embeddings = bool(job.get("generate_embeddings", True)) if job else True
            await self.queue_finalize_extraction(
                job_id=job_id,
                source_id=source_id,
                database_name=database_name,
                generate_embeddings=generate_embeddings,
                priority=settings.priorities.background,
            )
            logger.info(
                "auto_finalize_triggered",
                job_id=job_id,
                source_id=source_id,
                total_chunks=total,
                completed_chunks=progress["completed"],
                failed_chunks=progress["failed"],
            )

    async def _handle_chunk_cancellation(
        self,
        adapter: SqliteAdapter,
        data: dict[str, Any],
        chunk_task_id: str,
        job_id: str,
        database_name: str,
        chunk_index: int,
        settings: Settings,
    ) -> dict[str, Any]:
        """Handle CancelledError for chunk extraction (retry or fail permanently).

        Args:
            adapter: Storage adapter.
            data: Original task data (for re-queuing).
            chunk_task_id: Chunk task ID.
            job_id: Extraction job ID.
            database_name: Database context.
            chunk_index: Chunk index.
            settings: Application settings.

        Returns:
            Result dictionary.
        """
        current_task = adapter.get_chunk_task(chunk_task_id)
        current_retries = current_task.get("retry_count", 0) if current_task else 0

        if current_retries < settings.retries.extraction_chunk_max:
            logger.warning(
                "extract_chunk_cancelled_requeuing",
                chunk_task_id=chunk_task_id,
                job_id=job_id,
                chunk_index=chunk_index,
                retry_count=current_retries + 1,
                max_retries=settings.retries.extraction_chunk_max,
                reason="worker_timeout_or_cancellation",
            )

            try:
                adapter.update_chunk_task(
                    chunk_task_id,
                    {
                        "status": "queued",
                        "retry_count": current_retries + 1,
                        "error_message": f"Retry {current_retries + 1}: timeout/cancellation",
                    },
                )
            except Exception as fail_exc:
                logger.warning(
                    "update_chunk_task_failed",
                    event_key="update_chunk_task_failed",
                    chunk_task_id=chunk_task_id,
                    job_id=job_id,
                    chunk_index=chunk_index,
                    original_exception_type="asyncio.CancelledError",
                    original_exception_message=f"Retry {current_retries + 1}: timeout/cancellation",
                    fail_exception_type=type(fail_exc).__name__,
                    fail_exception_message=str(fail_exc),
                )
                raise
            try:
                await self.queue_extract_chunk(
                    chunk_task_id=chunk_task_id,
                    job_id=job_id,
                    database_name=database_name,
                    chunk_index=chunk_index,
                    hierarchical_group_id=data.get("hierarchical_group_id"),
                    small_chunk_ids=data.get("small_chunk_ids"),
                    priority=settings.priorities.background,
                )
            except asyncio.CancelledError:
                # Graceful worker shutdown: the requeue call was itself
                # cancelled before it could enqueue.  Log the moment so
                # it's queryable, then re-raise — returning normally here
                # would leave the chunk in a half-cancelled / unrequeued
                # state with the worker thinking the task completed.
                logger.warning(
                    "chunk_requeue_cancelled_during_shutdown",
                    chunk_task_id=chunk_task_id,
                    job_id=job_id,
                    chunk_index=chunk_index,
                    retry_count=current_retries + 1,
                    database_name=database_name,
                )
                raise
            except Exception:
                # Workstream 8 (2026-05-07) — surface re-enqueue failures.
                # The DB has already been flipped to ``status='queued'``
                # so SourceRecovery will re-dispatch this chunk on its
                # 60s loop; logging the moment of failure makes the
                # silent miss queryable.
                logger.warning(
                    "chunk_requeue_enqueue_failed",
                    chunk_task_id=chunk_task_id,
                    job_id=job_id,
                    chunk_index=chunk_index,
                    retry_count=current_retries + 1,
                    exc_info=True,
                )

            return {
                "success": False,
                "chunk_task_id": chunk_task_id,
                "error": "Task cancelled, requeued for retry",
                "retry_count": current_retries + 1,
            }

        # Max retries exceeded
        logger.warning(
            "extract_chunk_cancelled_max_retries",
            chunk_task_id=chunk_task_id,
            job_id=job_id,
            chunk_index=chunk_index,
            retry_count=current_retries,
            max_retries=settings.retries.extraction_chunk_max,
            reason="max_retries_exceeded",
        )

        try:
            adapter.fail_chunk_task(
                task_id=chunk_task_id,
                error_message=f"Task cancelled after {current_retries + 1} attempts (timeout)",
                error_type="CancelledError",
            )
        except Exception as fail_exc:
            logger.warning(
                "fail_handler_raised",
                event_key="fail_handler_raised",
                chunk_task_id=chunk_task_id,
                job_id=job_id,
                chunk_index=chunk_index,
                original_exception_type="asyncio.CancelledError",
                original_exception_message=f"Task cancelled after {current_retries + 1} attempts (timeout)",
                fail_exception_type=type(fail_exc).__name__,
                fail_exception_message=str(fail_exc),
            )
            raise
        # Route through the shared counter + auto-finalize helper so
        # a terminal-on-failure job still gets its finalization
        # transaction. source_id is looked up from the job record.
        with suppress(Exception):
            job = adapter.get_extraction_job(job_id)
            source_id = job.get("source_id") if job else None
            if source_id:
                from chaoscypher_core.services.quality.counters import (
                    QualityCounter,
                    increment_quality_counter,
                )

                await increment_quality_counter(
                    adapter=adapter,
                    source_id=source_id,
                    database_name=database_name,
                    counter=QualityCounter.LLM_CHUNKS_TIMED_OUT,
                )
                await self._update_chunk_progress(
                    adapter=adapter,
                    job_id=job_id,
                    source_id=source_id,
                    database_name=database_name,
                    chunk_task_id=chunk_task_id,
                    chunk_index=chunk_index,
                    task_outcome="failed",
                    settings=settings,
                )

        return {
            "success": False,
            "chunk_task_id": chunk_task_id,
            "error": f"Task cancelled after {current_retries + 1} attempts",
            "max_retries_exceeded": True,
        }

    async def _handle_chunk_failure(
        self,
        adapter: SqliteAdapter,
        exc: Exception,
        chunk_task_id: str,
        job_id: str,
        database_name: str,
        chunk_index: int,
        settings: Settings,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Handle general exception during chunk extraction.

        Args:
            adapter: Storage adapter.
            exc: The exception that occurred.
            chunk_task_id: Chunk task ID.
            job_id: Extraction job ID.
            database_name: Database context.
            chunk_index: Chunk index.
            settings: Application settings.
            data: Original handler data dict; used to forward
                ``hierarchical_group_id`` and ``small_chunk_ids`` when
                requeuing a retryable failure.

        Returns:
            Result dictionary.
        """
        from chaoscypher_core.exceptions import LLMModelError, ModelCapabilityError

        error_message = str(exc)
        is_fatal_error = isinstance(exc, (ModelCapabilityError, LLMModelError))

        if is_fatal_error:
            logger.exception(
                "extract_chunk_fatal_llm_error",
                chunk_task_id=chunk_task_id,
                job_id=job_id,
                chunk_index=chunk_index,
                error_type=type(exc).__name__,
                error_message=error_message,
                model=getattr(exc, "model", None),
                capability=getattr(exc, "capability", None),
            )
        else:
            logger.exception(
                "extract_chunk_failed",
                chunk_task_id=chunk_task_id,
                job_id=job_id,
                chunk_index=chunk_index,
                error_type=type(exc).__name__,
                error_message=error_message,
            )

        # Retryable LLM transients (rate limit, transient service error)
        # mirror the cancellation-retry contract: requeue up to
        # settings.retries.extraction_chunk_max before failing
        # permanently. Fatal LLM errors and generic exceptions keep
        # the original fail-once behavior.
        is_retryable = not is_fatal_error and getattr(exc, "is_retryable", False)
        if is_retryable:
            current_task = adapter.get_chunk_task(chunk_task_id)
            current_retries = current_task.get("retry_count", 0) if current_task else 0
            if current_retries < settings.retries.extraction_chunk_max:
                logger.warning(
                    "extract_chunk_retryable_error_requeuing",
                    chunk_task_id=chunk_task_id,
                    job_id=job_id,
                    chunk_index=chunk_index,
                    retry_count=current_retries + 1,
                    max_retries=settings.retries.extraction_chunk_max,
                    error_type=type(exc).__name__,
                    error_message=error_message,
                )
                try:
                    adapter.update_chunk_task(
                        chunk_task_id,
                        {
                            "status": "queued",
                            "retry_count": current_retries + 1,
                            "error_message": (
                                f"Retry {current_retries + 1}: "
                                f"{type(exc).__name__}: {error_message}"
                            ),
                        },
                    )
                except Exception as fail_exc:
                    logger.warning(
                        "update_chunk_task_failed",
                        event_key="update_chunk_task_failed",
                        chunk_task_id=chunk_task_id,
                        job_id=job_id,
                        chunk_index=chunk_index,
                        original_exception_type=type(exc).__name__,
                        original_exception_message=error_message,
                        fail_exception_type=type(fail_exc).__name__,
                        fail_exception_message=str(fail_exc),
                    )
                    raise
                _data = data or {}
                try:
                    await self.queue_extract_chunk(
                        chunk_task_id=chunk_task_id,
                        job_id=job_id,
                        database_name=database_name,
                        chunk_index=chunk_index,
                        hierarchical_group_id=_data.get("hierarchical_group_id"),
                        small_chunk_ids=_data.get("small_chunk_ids"),
                        priority=settings.priorities.background,
                    )
                except Exception:
                    # Workstream 8 (2026-05-07) — surface re-enqueue
                    # failures on the retryable-error path. SourceRecovery
                    # picks up orphaned chunks on its 60s loop; we just
                    # stop pretending the moment of failure didn't happen.
                    logger.warning(
                        "chunk_retry_requeue_failed",
                        chunk_task_id=chunk_task_id,
                        job_id=job_id,
                        chunk_index=chunk_index,
                        retry_count=current_retries + 1,
                        original_error_type=type(exc).__name__,
                        exc_info=True,
                    )
                return {
                    "success": False,
                    "chunk_task_id": chunk_task_id,
                    "error": f"Retryable error, requeued: {error_message}",
                    "retry_count": current_retries + 1,
                }
            logger.warning(
                "extract_chunk_retryable_max_retries",
                chunk_task_id=chunk_task_id,
                job_id=job_id,
                chunk_index=chunk_index,
                retry_count=current_retries,
                max_retries=settings.retries.extraction_chunk_max,
                error_type=type(exc).__name__,
            )
            # fall through to the existing fail_chunk_task path; mark
            # max_retries_exceeded in the return dict for observability
            try:
                adapter.fail_chunk_task(
                    task_id=chunk_task_id,
                    error_message=(
                        f"{type(exc).__name__} after {current_retries + 1} attempts: "
                        f"{error_message}"
                    ),
                    error_type=type(exc).__name__,
                )
            except Exception as fail_exc:
                logger.warning(
                    "fail_handler_raised",
                    event_key="fail_handler_raised",
                    chunk_task_id=chunk_task_id,
                    job_id=job_id,
                    chunk_index=chunk_index,
                    original_exception_type=type(exc).__name__,
                    original_exception_message=error_message,
                    fail_exception_type=type(fail_exc).__name__,
                    fail_exception_message=str(fail_exc),
                )
                raise
            with suppress(Exception):
                job = adapter.get_extraction_job(job_id)
                source_id = job.get("source_id") if job else None
                if source_id:
                    from chaoscypher_core.services.quality.counters import (
                        QualityCounter,
                        increment_quality_counter,
                    )

                    await increment_quality_counter(
                        adapter=adapter,
                        source_id=source_id,
                        database_name=database_name,
                        counter=QualityCounter.LLM_CHUNKS_FAILED_PERMANENT,
                    )
                    await self._update_chunk_progress(
                        adapter=adapter,
                        job_id=job_id,
                        source_id=source_id,
                        database_name=database_name,
                        chunk_task_id=chunk_task_id,
                        chunk_index=chunk_index,
                        task_outcome="failed",
                        settings=settings,
                    )
            return {
                "success": False,
                "chunk_task_id": chunk_task_id,
                "error": f"{type(exc).__name__} after max retries: {error_message}",
                "max_retries_exceeded": True,
            }

        try:
            adapter.fail_chunk_task(
                task_id=chunk_task_id,
                error_message=error_message,
                error_type=type(exc).__name__,
            )
        except Exception as fail_exc:
            logger.warning(
                "fail_handler_raised",
                event_key="fail_handler_raised",
                chunk_task_id=chunk_task_id,
                job_id=job_id,
                chunk_index=chunk_index,
                original_exception_type=type(exc).__name__,
                original_exception_message=error_message,
                fail_exception_type=type(fail_exc).__name__,
                fail_exception_message=str(fail_exc),
            )
            raise

        if is_fatal_error:
            try:
                adapter.fail_extraction_job(job_id, error_message)
            except Exception as fail_exc:
                logger.warning(
                    "fail_handler_raised",
                    event_key="fail_handler_raised",
                    chunk_task_id=chunk_task_id,
                    job_id=job_id,
                    chunk_index=chunk_index,
                    original_exception_type=type(exc).__name__,
                    original_exception_message=error_message,
                    fail_exception_type=type(fail_exc).__name__,
                    fail_exception_message=str(fail_exc),
                )
                raise
            job = adapter.get_extraction_job(job_id)
            if job:
                try:
                    adapter.fail_extraction(job["source_id"], f"Extraction failed: {error_message}")
                except Exception as fail_exc:
                    logger.warning(
                        "fail_handler_raised",
                        event_key="fail_handler_raised",
                        chunk_task_id=chunk_task_id,
                        job_id=job_id,
                        source_id=job["source_id"],
                        chunk_index=chunk_index,
                        original_exception_type=type(exc).__name__,
                        original_exception_message=error_message,
                        fail_exception_type=type(fail_exc).__name__,
                        fail_exception_message=str(fail_exc),
                    )
                    raise
        else:
            # Route through the shared counter + auto-finalize helper
            # so a terminal-on-failure job still gets its finalization
            # transaction. source_id is looked up from the job record.
            with suppress(Exception):
                job = adapter.get_extraction_job(job_id)
                source_id = job.get("source_id") if job else None
                if source_id:
                    from chaoscypher_core.services.quality.counters import (
                        QualityCounter,
                        increment_quality_counter,
                    )

                    await increment_quality_counter(
                        adapter=adapter,
                        source_id=source_id,
                        database_name=database_name,
                        counter=QualityCounter.LLM_CHUNKS_FAILED_PERMANENT,
                    )
                    await self._update_chunk_progress(
                        adapter=adapter,
                        job_id=job_id,
                        source_id=source_id,
                        database_name=database_name,
                        chunk_task_id=chunk_task_id,
                        chunk_index=chunk_index,
                        task_outcome="failed",
                        settings=settings,
                    )

        return {
            "success": False,
            "chunk_task_id": chunk_task_id,
            "error": error_message,
            "is_capability_error": is_fatal_error,
        }


# ------------------------------------------------------------------
# Module-level helpers (stateless, used by _extract_chunk_handler)
# ------------------------------------------------------------------


async def _compute_chunk_entity_embeddings(
    entities: list[dict[str, Any]],
    embedding_service: Any,
) -> list[list[float]] | None:
    """Embed chunk entities for cached-reuse during finalize.

    Persisted alongside ``raw_entities`` on the chunk task row so a
    finalize-handler crash doesn't trigger re-embedding the aggregated
    entity set on retry.

    Returns ``None`` when the embedding service is unavailable, the
    entity list is empty, or the call raises — callers store ``None``
    in that case and the finalize-time backfill picks it up.
    """
    if not embedding_service or not entities:
        return None
    from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
        entity_to_embedding_text,
    )

    texts = [entity_to_embedding_text(e) for e in entities]
    try:
        result = await embedding_service.batch_embed(texts)
        return list(result.embeddings)
    except Exception as exc:
        logger.warning(
            "chunk_entity_embedding_failed",
            entity_count=len(entities),
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return None


def _check_stale_chunk_task(
    adapter: SqliteAdapter,
    chunk_task_id: str,
    job_id: str,
    chunk_index: int,
) -> dict[str, Any] | None:
    """Check if chunk task is stale/orphaned and should be skipped.

    Args:
        adapter: Storage adapter.
        chunk_task_id: Chunk task ID.
        job_id: Parent extraction job ID.
        chunk_index: Chunk index for logging.

    Returns:
        Skip result dict if stale, or ``None`` to proceed.
    """
    job = adapter.get_extraction_job(job_id)
    if not job:
        logger.debug(
            "extract_chunk_skipped_job_not_found",
            chunk_task_id=chunk_task_id,
            job_id=job_id,
            chunk_index=chunk_index,
            reason="job_record_missing",
        )
        return {
            "success": False,
            "skipped": True,
            "reason": "job_not_found",
            "chunk_task_id": chunk_task_id,
        }

    if job.get("status") in ("completed", "failed", "cancelled"):
        logger.info(
            "extract_chunk_skipped_job_finished",
            chunk_task_id=chunk_task_id,
            job_id=job_id,
            job_status=job.get("status"),
            chunk_index=chunk_index,
            reason="job_already_finalized",
        )
        return {
            "success": False,
            "skipped": True,
            "reason": "job_already_finished",
            "chunk_task_id": chunk_task_id,
        }

    chunk_task = adapter.get_chunk_task(chunk_task_id)
    if not chunk_task:
        logger.warning(
            "extract_chunk_skipped_task_not_found",
            chunk_task_id=chunk_task_id,
            job_id=job_id,
            chunk_index=chunk_index,
            reason="chunk_task_record_missing",
        )
        return {
            "success": False,
            "skipped": True,
            "reason": "task_not_found",
            "chunk_task_id": chunk_task_id,
        }

    if chunk_task.get("status") == "completed":
        # INFO rather than WARNING: a re-dispatched chunk whose DB row
        # is already in a terminal state is expected (both the queue
        # reconciler and the source reconciler can trigger this path),
        # not an anomaly.
        logger.info(
            "extract_chunk_skipped_already_completed",
            chunk_task_id=chunk_task_id,
            job_id=job_id,
            chunk_index=chunk_index,
            reason="chunk_task_already_completed",
        )
        return {
            "success": False,
            "skipped": True,
            "reason": "task_already_completed",
            "chunk_task_id": chunk_task_id,
        }

    return None


def _store_job_prompts(
    adapter: SqliteAdapter, job_id: str, extraction_metrics: dict[str, Any]
) -> None:
    """Store shared prompt templates on the job record (once).

    Reads from ``_prompt_data`` sub-dict (set by ``extract_single_chunk``) and
    pops it from the metrics dict to free memory.

    Args:
        adapter: Storage adapter.
        job_id: Extraction job ID.
        extraction_metrics: Extraction metrics containing ``_prompt_data``.
    """
    prompt_data = extraction_metrics.pop("_prompt_data", None) or {}
    job_record = adapter.get_extraction_job(job_id)
    if not job_record:
        return
    if not job_record.get("system_prompt"):
        adapter.update_extraction_job(
            job_id,
            {
                "system_prompt": prompt_data.get("system_prompt"),
                "user_instructions": prompt_data.get("user_instructions"),
                "relationship_instructions": prompt_data.get("relationship_instructions"),
                "extraction_rules_template": prompt_data.get("extraction_rules_template"),
                "entity_templates": prompt_data.get("entity_templates"),
                "relationship_templates": prompt_data.get("relationship_templates"),
                "domain_guidance": prompt_data.get("domain_guidance"),
                "domain_examples": prompt_data.get("domain_examples"),
            },
        )
