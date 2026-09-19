# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``_update_chunk_progress``'s SQLite writes must not run on the event loop.

``increment_job_completed_and_check`` and ``update_step_progress`` are plain
``def`` adapter methods that issue UPDATE + COMMIT. A COMMIT that loses the
SQLite writer lock parks its caller inside the C-level busy handler, and on
timeout ``SafeSession._retry_delay`` adds ``time.sleep`` backoff on top — up to
``busy_timeout_ms`` plus ~15 s. Run directly from the coroutine that awaits
this method, that blocks the whole Operations-queue event loop, including the
heartbeat refresher whose lapse makes the reconciler judge live tasks
abandoned.

The chunk-persist transaction 150 lines earlier in the same function is
already offloaded for exactly this reason (``asyncio.to_thread``, with a
rationale comment). These tests pin the same contract for the progress tail by
recording which thread each adapter call lands on.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _recording_adapter(threads: dict[str, int], *, is_terminal: bool) -> MagicMock:
    """Adapter whose blocking methods record the thread they were called on."""
    adapter = MagicMock()

    def _increment(**_: Any) -> dict[str, Any]:
        threads["increment"] = threading.get_ident()
        return {"completed": 3, "failed": 0, "total": 3, "is_terminal": is_terminal}

    def _step_progress(*_: Any, **__: Any) -> None:
        threads["step_progress"] = threading.get_ident()

    def _get_job(*_: Any, **__: Any) -> dict[str, Any]:
        threads["get_job"] = threading.get_ident()
        return {"id": "job-1", "source_id": "src-1", "generate_embeddings": True}

    adapter.increment_job_completed_and_check = MagicMock(side_effect=_increment)
    adapter.update_step_progress = MagicMock(side_effect=_step_progress)
    adapter.get_extraction_job = MagicMock(side_effect=_get_job)
    return adapter


async def _run(adapter: MagicMock) -> None:
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    settings = MagicMock()
    settings.priorities.background = 50

    service = ChunkExtractionOperationsService(source_repository=adapter)
    with patch.object(
        service, "queue_finalize_extraction", new=AsyncMock(return_value="finalize-t")
    ):
        await service._update_chunk_progress(
            adapter=adapter,
            job_id="job-1",
            source_id="src-1",
            database_name="default",
            chunk_task_id="t-1",
            chunk_index=0,
            task_outcome="completed",
            settings=settings,
        )


@pytest.mark.asyncio
async def test_counter_and_step_progress_writes_leave_the_event_loop() -> None:
    """The two writer-lock calls run on a worker thread, not the loop thread."""
    threads: dict[str, int] = {}
    adapter = _recording_adapter(threads, is_terminal=False)

    await _run(adapter)

    loop_thread = threading.get_ident()
    assert threads["increment"] != loop_thread
    assert threads["step_progress"] != loop_thread


@pytest.mark.asyncio
async def test_terminal_job_read_leaves_the_event_loop() -> None:
    """The terminal-branch ``get_extraction_job`` read is offloaded too.

    It is the third unprojected read on this path and shares the same session,
    so it contends for the same lock as the writes above.
    """
    threads: dict[str, int] = {}
    adapter = _recording_adapter(threads, is_terminal=True)

    await _run(adapter)

    assert threads["get_job"] != threading.get_ident()


@pytest.mark.asyncio
async def test_offload_preserves_the_finalize_enqueue() -> None:
    """Offloading must not change the terminal-transition contract."""
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    threads: dict[str, int] = {}
    adapter = _recording_adapter(threads, is_terminal=True)

    settings = MagicMock()
    settings.priorities.background = 50

    service = ChunkExtractionOperationsService(source_repository=adapter)
    with patch.object(
        service, "queue_finalize_extraction", new=AsyncMock(return_value="finalize-t")
    ) as mock_finalize:
        await service._update_chunk_progress(
            adapter=adapter,
            job_id="job-1",
            source_id="src-1",
            database_name="default",
            chunk_task_id="t-1",
            chunk_index=0,
            task_outcome="completed",
            settings=settings,
        )

    mock_finalize.assert_awaited_once()
    assert mock_finalize.await_args.kwargs["job_id"] == "job-1"
    assert mock_finalize.await_args.kwargs["generate_embeddings"] is True
