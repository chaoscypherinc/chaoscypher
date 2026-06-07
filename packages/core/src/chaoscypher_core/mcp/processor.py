# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Background document processing queue for MCP.

Provides a simple in-memory FIFO queue that processes uploaded documents
one at a time. Files are indexed (chunked + embedded) and optionally
extracted (entity recognition + graph commit) in the background.
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from chaoscypher_core.models import SourceStatus
from chaoscypher_core.utils.id import generate_id


logger = structlog.get_logger(__name__)


@dataclass
class QueuedFile:
    """A file waiting to be processed."""

    file_id: str
    file_path: str
    filename: str
    extraction_depth: str = "full"
    forced_domain: str | None = None
    # Tri-state: ``None`` defers to ``resolve_normalization_default(filename)``
    # at indexing time, matching the CLI and Cortex queue paths. Explicit
    # ``True`` / ``False`` is a user override the pipeline must honour.
    # Pre-2026-05 default was hardcoded ``True`` which corrupted CSV / JSON /
    # XML uploads by stripping their structural whitespace.
    enable_normalization: bool | None = None
    skip_duplicates: bool = False
    is_url: bool = False
    content: str | None = None
    enable_vision: bool | None = None
    # Bypass the domain-confirmation gate. Default ``True`` preserves the
    # fire-and-forget behaviour for the index-only pipeline (no gate) and lets
    # the full pipeline extract immediately unless a caller opts into the gate.
    auto_confirm: bool = True


@dataclass
class ProcessingStatus:
    """Status of the currently processing file."""

    file_id: str
    filename: str
    status: str = "starting"
    step: int = 0
    total_steps: int = 5
    error: str | None = None


@dataclass
class CompletedFile:
    """A completed (or failed) file."""

    file_id: str
    filename: str
    status: str
    source_id: str = ""
    nodes_created: int = 0
    edges_created: int = 0
    error: str | None = None


class DocumentProcessor:
    """In-memory FIFO document processing queue.

    Processes one document at a time via an asyncio background task.
    The pipeline_callback handles the actual indexing/extraction work.
    """

    def __init__(
        self,
        pipeline_callback: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
        completed_history_limit: int = 20,
    ) -> None:
        """Initialize with a processing pipeline callback.

        Args:
            pipeline_callback: Async function that processes a file.
                Signature: (file_path: str, file_id: str, progress_cb) -> dict
            completed_history_limit: Max completed files retained in memory.
                Callers pass settings.mcp.completed_history_limit.

        """
        self.pipeline_callback = pipeline_callback
        self._completed_history_limit = completed_history_limit
        self._queue: list[QueuedFile] = []
        self._current: ProcessingStatus | None = None
        self._completed: list[CompletedFile] = []
        self._worker_task: asyncio.Task | None = None
        self._running = False
        self._completion_events: dict[str, asyncio.Event] = {}

    async def add_document(
        self,
        file_path: str,
        extraction_depth: str = "full",
        forced_domain: str | None = None,
        enable_normalization: bool | None = None,
        skip_duplicates: bool = False,
        wait: bool = False,
        wait_timeout: float = 300,
        content: str | None = None,
        enable_vision: bool | None = None,
        auto_confirm: bool = True,
    ) -> dict[str, Any]:
        """Add a document to the processing queue.

        Args:
            file_path: Path to the file to process, or an HTTP(S) URL.
            extraction_depth: Extraction depth ('quick' or 'full').
            forced_domain: Force a specific domain, or None for auto-detect.
            auto_confirm: Bypass the domain-confirmation gate (default True).
                Forwarded to the pipeline callback; the full pipeline parks an
                auto-detected source when this is False.
            enable_normalization: Clean OCR artifacts and normalize whitespace.
            skip_duplicates: Check content hash to skip identical uploads.
            wait: If True, block until processing completes.
            wait_timeout: Maximum seconds to wait (only used when wait=True).
            content: Pre-processed text content. Skips loader pipeline when provided.
            enable_vision: Enable vision processing. None=auto, True=force, False=skip.

        Returns:
            Dict with status ('processing' or 'queued'), file_id, and position.
            When wait=True, returns the final completion result instead.

        """
        is_url = file_path.startswith(("http://", "https://")) if file_path else False
        if not content and not is_url and file_path:
            file_exists = await asyncio.to_thread(Path(file_path).exists)
            if not file_exists:
                return {"success": False, "error": "File not found"}

        file_id = generate_id()
        filename = file_path if is_url else Path(file_path).name
        queued = QueuedFile(
            file_id=file_id,
            file_path=file_path,
            filename=filename,
            extraction_depth=extraction_depth,
            forced_domain=forced_domain,
            enable_normalization=enable_normalization,
            skip_duplicates=skip_duplicates,
            is_url=is_url,
            content=content,
            enable_vision=enable_vision,
            auto_confirm=auto_confirm,
        )

        self._queue.append(queued)
        self._completion_events[file_id] = asyncio.Event()

        # Start worker if not running (check both flag and task liveness)
        worker_alive = (
            self._running and self._worker_task is not None and not self._worker_task.done()
        )
        if not worker_alive:
            self._start_worker()
            immediate_result = {
                "success": True,
                "status": "processing",
                "file_id": file_id,
                "filename": filename,
                "position": 0,
            }
        else:
            # Position includes the currently-processing item (offset by 1)
            # so the first queued item reports position=1 (one item is ahead)
            position = len(self._queue) - 1 + (1 if self._current is not None else 0)
            immediate_result = {
                "success": True,
                "status": "queued",
                "file_id": file_id,
                "filename": filename,
                "position": position,
            }

        if wait:
            return await self.wait_for_completion(file_id, wait_timeout)

        return immediate_result

    def get_status(self) -> dict[str, Any]:
        """Get current processing status.

        Returns:
            Dict with current, queued, and completed file info.

        """
        current = None
        if self._current:
            current = {
                "file_id": self._current.file_id,
                "filename": self._current.filename,
                "status": self._current.status,
                "step": self._current.step,
                "total_steps": self._current.total_steps,
                "error": self._current.error,
            }

        queued = [
            {"file_id": q.file_id, "filename": q.filename, "position": i}
            for i, q in enumerate(self._queue)
        ]

        completed = [
            {
                "file_id": c.file_id,
                "filename": c.filename,
                "status": c.status,
                "nodes_created": c.nodes_created,
                "edges_created": c.edges_created,
                "error": c.error,
            }
            for c in self._completed[-self._completed_history_limit :]
        ]

        return {"current": current, "queued": queued, "completed": completed}

    def cancel(self) -> None:
        """Cancel the worker task."""
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()

    def has_pending(self, file_id: str) -> bool:
        """Check if a file_id is pending completion.

        Args:
            file_id: ID of the document to check.

        Returns:
            True if the file is queued or currently processing.

        """
        return file_id in self._completion_events

    def get_completed(self, file_id: str) -> dict[str, Any] | None:
        """Get completion result for a file_id.

        Args:
            file_id: ID of the document to look up.

        Returns:
            Dict with status and result details, or None if not found.

        """
        for c in self._completed:
            if c.file_id == file_id:
                return self._completed_to_dict(c)
        return None

    @staticmethod
    def _completed_to_dict(c: CompletedFile) -> dict[str, Any]:
        """Convert a CompletedFile to a result dict.

        Args:
            c: Completed file record.

        Returns:
            Dict with file_id, source_id, status, and result details.

        """
        return {
            "file_id": c.file_id,
            "source_id": c.source_id or c.file_id,
            "status": c.status,
            "nodes_created": c.nodes_created,
            "edges_created": c.edges_created,
            "error": c.error,
        }

    async def wait_for_completion(self, file_id: str, timeout: float = 300) -> dict[str, Any]:
        """Block until a specific document finishes processing.

        Args:
            file_id: ID of the document to wait for.
            timeout: Maximum seconds to wait before timing out.

        Returns:
            Dict with file_id, status, and result details.

        """
        event = self._completion_events.get(file_id)
        if event is None:
            # Already completed (or unknown) — check completed list
            for c in self._completed:
                if c.file_id == file_id:
                    return self._completed_to_dict(c)
            return {"file_id": file_id, "status": "unknown", "error": "File ID not found"}

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return {"file_id": file_id, "status": "timeout", "error": f"Timed out after {timeout}s"}

        # Find result in completed list
        for c in self._completed:
            if c.file_id == file_id:
                return self._completed_to_dict(c)

        return {"file_id": file_id, "status": "completed"}

    def _start_worker(self) -> None:
        """Start the background worker loop."""
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        """Process queued files one at a time."""
        while self._running and self._queue:
            queued = self._queue.pop(0)
            self._current = ProcessingStatus(
                file_id=queued.file_id,
                filename=queued.filename,
                status="processing",
            )

            try:
                # Await the callback; if the result is itself a coroutine
                # (e.g. AsyncMock side_effect returning asyncio.sleep()),
                # await it too so slow mocks behave as expected in tests.
                result = await self.pipeline_callback(
                    file_path=queued.file_path,
                    file_id=queued.file_id,
                    progress_callback=self._update_progress,
                    extraction_depth=queued.extraction_depth,
                    forced_domain=queued.forced_domain,
                    enable_normalization=queued.enable_normalization,
                    skip_duplicates=queued.skip_duplicates,
                    is_url=queued.is_url,
                    auto_confirm=queued.auto_confirm,
                )
                if asyncio.iscoroutine(result):
                    result = await result
                # ``ProcessingResult.model_dump()`` now carries an explicit
                # ``status`` (``None`` on the normal extract-and-commit path,
                # ``awaiting_confirmation`` when parked). Treat falsy as the
                # historical default so the full pipeline still reports
                # committed when extraction ran.
                self._completed.append(
                    CompletedFile(
                        file_id=queued.file_id,
                        filename=queued.filename,
                        status=result.get("status") or SourceStatus.COMMITTED,
                        source_id=result.get("source_id", queued.file_id),
                        nodes_created=result.get("nodes_created", 0),
                        edges_created=result.get("edges_created", 0),
                    )
                )
            except Exception as e:
                logger.exception(
                    "document_processing_failed",
                    file_id=queued.file_id,
                    error=str(e),
                )
                self._completed.append(
                    CompletedFile(
                        file_id=queued.file_id,
                        filename=queued.filename,
                        status="failed",
                        error=f"Document processing failed: {e}",
                    )
                )
            finally:
                self._current = None
                event = self._completion_events.pop(queued.file_id, None)
                if event:
                    event.set()

        self._running = False

    def _update_progress(self, step: int, total: int, status: str) -> None:
        """Update progress of current file."""
        if self._current:
            self._current.step = step
            self._current.total_steps = total
            self._current.status = status
