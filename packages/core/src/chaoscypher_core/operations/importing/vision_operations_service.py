# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Vision operations service.

Registers both vision-pipeline operation handlers:

* ``OP_VISION_PAGE`` on ``QUEUE_LLM`` (per-page describe — LLM-bound).
* ``OP_VISION_FINALIZE`` on ``QUEUE_OPERATIONS`` (merge + state
  transition — no LLM work).

Mirrors the ``ChunkExtractionOperationsService`` shape — adapter,
settings, and database_name are injected at construction time; the
bound methods ``_handle_vision_page`` and ``_handle_vision_finalize``
satisfy the TaskHandler protocol (data, metadata=, task_id=).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import (
    OP_VISION_FINALIZE,
    OP_VISION_PAGE,
    QUEUE_LLM,
    QUEUE_OPERATIONS,
)
from chaoscypher_core.operations.importing.vision_finalizer import (
    handle_vision_finalize,
)
from chaoscypher_core.operations.importing.vision_page_handler import (
    _enqueue_finalize,
    _get_active_vision_model,
    _get_vision_max_output_tokens,
    _persist_page_image,
    _render_image_bytes,
)
from chaoscypher_core.queue import queue_client
from chaoscypher_core.queue.handler_spec import HandlerSpec
from chaoscypher_core.services.quality.counters import (
    QualityCounter,
    increment_quality_counter,
)
from chaoscypher_core.services.vision.service import VisionService, create_vision_provider
from chaoscypher_core.vision.states import VisionPageStatus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.app_config import Settings


logger = structlog.get_logger(__name__)


class VisionOperationsService:
    """Hosts vision-pipeline operation handlers.

    Adapter, settings, and database_name are injected at construction
    time (mirroring ChunkExtractionOperationsService). The bound method
    ``_handle_vision_page`` satisfies the TaskHandler protocol and is
    registered as the OP_VISION_PAGE handler.

    Registers:
    - OP_VISION_PAGE on QUEUE_LLM (per-page describe — LLM-bound).
    - OP_VISION_FINALIZE on QUEUE_OPERATIONS (merge + state transition — no LLM).
    """

    def __init__(
        self,
        adapter: SqliteAdapter | None = None,
        settings: Settings | None = None,
        database_name: str = "default",
        vision_service: VisionService | None = None,
    ) -> None:
        """Build the handler dict.

        Args:
            adapter: Storage adapter used by the handlers.
            settings: Application settings.
            database_name: Default database context for this worker.
            vision_service: Optional VisionService override (test injection;
                production path builds one from settings on first call).

        retry_on_crash=True for both handlers:
        - _handle_vision_page: idempotent via row status guard +
          single-terminal-observation guarantee in
          increment_vision_job_completed_and_check.
        - _handle_vision_finalize: idempotent via the SourceStatus
          VISION_PENDING short-circuit + transition_source_status CAS.
        """
        self.adapter = adapter
        self.settings = settings
        self.database_name = database_name
        self._vision_service_override = vision_service

        self.operation_handlers = {
            OP_VISION_PAGE: HandlerSpec(
                handler=self._handle_vision_page,
                retry_on_crash=True,
            ),
        }
        self.finalize_handlers = {
            OP_VISION_FINALIZE: HandlerSpec(
                handler=self._handle_vision_finalize,
                retry_on_crash=True,
            ),
        }
        logger.info("vision_operations_service_initialized")

    def register_handlers(self) -> None:
        """Register vision handlers on the appropriate queues.

        - OP_VISION_PAGE on QUEUE_LLM.
        - OP_VISION_FINALIZE on QUEUE_OPERATIONS (no LLM work — merge
          + state transition only).
        """
        queue_client.register_handlers(QUEUE_LLM, self.operation_handlers)  # type: ignore[arg-type]
        queue_client.register_handlers(QUEUE_OPERATIONS, self.finalize_handlers)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Operation handler
    # ------------------------------------------------------------------

    async def _mark_page_failed_and_advance_job(
        self,
        *,
        page_id: str,
        job_id: str,
        source_id: str,
        error_message: str,
    ) -> None:
        """Mark a page FAILED and advance the job counter (enqueueing finalize if terminal).

        Shared between the render-failed and no-model-configured branches so
        both follow the same idempotent update + best-effort-advance pattern.
        """
        rows_affected = await asyncio.to_thread(
            self.adapter.update_vision_page_description,
            page_id=page_id,
            new_status=VisionPageStatus.FAILED,
            description=None,
            finish_reason=None,
            error_message=error_message,
        )
        if rows_affected == 0:
            return
        progress = await asyncio.to_thread(
            self.adapter.increment_vision_job_completed_and_check,
            job_id=job_id,
            outcome=VisionPageStatus.FAILED,
        )
        if progress["is_terminal"]:
            await _enqueue_finalize(
                source_id=source_id,
                job_id=job_id,
                database_name=self.database_name,
            )

    async def _resolve_vision_service(
        self,
        *,
        page_id: str,
        job_id: str,
        source_id: str,
    ) -> VisionService | None:
        """Return the VisionService for this task, or ``None`` if no model is configured.

        When no model is configured, marks the page FAILED and advances the
        job counter (matching the legacy stall-prevention bug fix).
        """
        if self._vision_service_override is not None:
            return self._vision_service_override
        vision_model = _get_active_vision_model(self.settings)
        if vision_model is None:
            logger.error("vision_page_handler_no_model_configured", page_id=page_id)
            await self._mark_page_failed_and_advance_job(
                page_id=page_id,
                job_id=job_id,
                source_id=source_id,
                error_message="no vision_model configured for provider",
            )
            return None
        vision_provider = create_vision_provider(self.settings, vision_model)
        return VisionService(llm_provider=vision_provider)

    async def _handle_vision_page(  # noqa: PLR0911 - one return per terminal page outcome (missing/stale/render/persist/no-model/cap/success)
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Handle one OP_VISION_PAGE queue task.

        Dispatched as ``handler(data, metadata=metadata, task_id=task_id)``
        by the queue dispatcher (satisfies TaskHandler protocol).

        Args:
            data: Task payload — ``{"page_id", "job_id", "source_id"}``.
            metadata: Queue task metadata (unused by handler body).
            task_id: Queue task ID (unused by handler body).

        Returns:
            Result dict with ``"status"`` and optional ``"is_terminal"``.
        """
        adapter = self.adapter
        settings = self.settings
        database_name = self.database_name

        page_id: str = data["page_id"]
        job_id: str = data["job_id"]
        source_id: str = data["source_id"]

        # 1. Load the row by scanning all rows for this source and filtering.
        # list_vision_page_descriptions returns rows ordered by (page_number, region_index).
        rows = await asyncio.to_thread(adapter.list_vision_page_descriptions, source_id)
        row = next((r for r in rows if r["id"] == page_id), None)
        if row is None:
            logger.warning("vision_page_handler_row_missing", page_id=page_id)
            return {"status": "skipped_missing"}

        if row["status"] != VisionPageStatus.PENDING.value:
            logger.info(
                "vision_page_handler_stale_dispatch",
                page_id=page_id,
                current_status=row["status"],
            )
            return {"status": "skipped_stale"}

        # 2. Render / read image bytes.
        try:
            image_bytes = await asyncio.to_thread(
                _render_image_bytes, row, dpi=settings.llm.vision_image_dpi
            )
        except Exception as exc:
            logger.warning(
                "vision_page_handler_render_failed",
                page_id=page_id,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            await self._mark_page_failed_and_advance_job(
                page_id=page_id,
                job_id=job_id,
                source_id=source_id,
                error_message=f"render_failed: {type(exc).__name__}: {exc}",
            )
            return {"status": "render_failed"}

        # 2b. Persist the rendered PNG to the canonical UI-served location.
        # Best-effort: a disk-write failure must not block the LLM call.
        # cleanup_vision_images sweeps the directory on indexing failure /
        # source delete.
        try:
            await asyncio.to_thread(
                _persist_page_image,
                image_bytes,
                data_dir=settings.paths.data_dir,
                database_name=database_name,
                source_id=source_id,
                page_number=row["page_number"],
            )
        except Exception:
            logger.warning(
                "vision_page_image_persist_failed",
                page_id=page_id,
                source_id=source_id,
                page_number=row["page_number"],
                exc_info=True,
            )

        # 3. Call vision LLM.
        vision_service = await self._resolve_vision_service(
            page_id=page_id, job_id=job_id, source_id=source_id
        )
        if vision_service is None:
            return {"status": "no_model"}

        # Enforce the LLM spend cap before the billable vision call. Vision is
        # source-scoped, so the per-source cap applies alongside the daily one.
        # LLMSpendCapExceededError is permanent (is_retryable=False): mark the
        # page FAILED and advance the job rather than letting the queue retry
        # into an exhausted budget.
        from chaoscypher_core.exceptions import LLMSpendCapExceededError
        from chaoscypher_core.services.llm.spend import get_llm_spend_tracker

        try:
            get_llm_spend_tracker().check_and_raise(
                source_id=source_id,
                settings=settings,
                adapter=adapter,
                database_name=database_name,
            )
        except LLMSpendCapExceededError as exc:
            logger.warning(
                "vision_page_handler_spend_cap_exceeded",
                page_id=page_id,
                source_id=source_id,
                error_message=exc.message,
            )
            await self._mark_page_failed_and_advance_job(
                page_id=page_id,
                job_id=job_id,
                source_id=source_id,
                error_message=f"spend_cap_exceeded: {exc.message}",
            )
            return {"status": "spend_cap_exceeded"}

        max_tokens = _get_vision_max_output_tokens(settings)
        result = await vision_service.describe_image(
            image_bytes=image_bytes,
            max_tokens=max_tokens,
        )

        # Record vision token usage so the daily/per-source spend cap observes
        # this call. Best-effort: record() never raises into the handler.
        get_llm_spend_tracker().record(
            source_id,
            result.input_tokens + result.output_tokens,
            adapter=adapter,
            database_name=database_name,
        )

        # 4. Map result to terminal outcome.
        if result.description is None:
            outcome = VisionPageStatus.FAILED
            description = None
            error_message = "vision LLM returned no content"
        elif result.finish_reason == "length":
            outcome = VisionPageStatus.TRUNCATED
            description = result.description
            error_message = None
        else:
            outcome = VisionPageStatus.SUCCEEDED
            description = result.description
            error_message = None

        # 5. Atomic guarded update.
        rows_affected = await asyncio.to_thread(
            adapter.update_vision_page_description,
            page_id=page_id,
            new_status=outcome,
            description=description,
            finish_reason=result.finish_reason,
            error_message=error_message,
        )
        if rows_affected == 0:
            logger.info(
                "vision_page_handler_stale_dispatch",
                page_id=page_id,
                outcome=outcome.value,
            )
            return {"status": "skipped_stale"}

        # 6. Bump quality counter for truncations (best-effort via the typed helper).
        if outcome == VisionPageStatus.TRUNCATED:
            await increment_quality_counter(
                adapter=adapter,
                source_id=source_id,
                database_name=database_name,
                counter=QualityCounter.VISION_PAGES_TRUNCATED,
                n=1,
            )

        # 7. Bump job counter; if terminal, enqueue finalize.
        progress = await asyncio.to_thread(
            adapter.increment_vision_job_completed_and_check,
            job_id=job_id,
            outcome=outcome,
        )

        logger.info(
            "vision_page_handler_complete",
            page_id=page_id,
            job_id=job_id,
            source_id=source_id,
            outcome=outcome.value,
            completed=progress["completed"],
            failed=progress["failed"],
            total=progress["total"],
            is_terminal=progress["is_terminal"],
        )

        if progress["is_terminal"]:
            await _enqueue_finalize(
                source_id=source_id,
                job_id=job_id,
                database_name=database_name,
            )

        return {"status": "success", "is_terminal": progress["is_terminal"]}

    # ------------------------------------------------------------------
    # Finalize handler
    # ------------------------------------------------------------------

    async def _handle_vision_finalize(
        self,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Handle one OP_VISION_FINALIZE queue task.

        Thin bound wrapper that injects ``adapter`` + ``settings`` into
        the module-level :func:`handle_vision_finalize` so the latter is
        directly callable from tests with explicit injection while the
        worker dispatcher uses the bound-method form (satisfies the
        TaskHandler protocol).
        """
        assert self.adapter is not None, "VisionOperationsService.adapter is required"
        assert self.settings is not None, "VisionOperationsService.settings is required"
        return await handle_vision_finalize(
            data,
            metadata=metadata,
            task_id=task_id,
            adapter=self.adapter,
            settings=self.settings,
        )


__all__ = ["VisionOperationsService"]
