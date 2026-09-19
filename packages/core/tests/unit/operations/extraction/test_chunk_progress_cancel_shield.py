# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A cancel must not throw away the finalize claim the increment paid for.

``_update_chunk_progress`` runs ``increment_job_completed_and_check`` on
``asyncio.to_thread``. That thread cannot be cancelled: when the worker
cancels the chunk task (``asyncio.wait_for``'s timeout, or the shutdown
drain's ``task.cancel()``) while the coroutine awaits it, the thread still
runs its UPDATE + COMMIT to completion while the awaiting coroutine drops
the ``progress`` dict on the floor.

That dict is not just a progress report: ``is_terminal`` is the atomic
*claim* of the finalize transition (``finalize_claimed = 0 -> 1``). Losing
it burns the claim — no other chunk handler, not even a re-delivery of this
one, can observe ``is_terminal`` again, so the job's auto-finalize enqueue
is gone until ``services/sources/recovery.py`` notices, at recovery latency.

The window runs from the start of the increment to the end of the enqueue
that spends the claim, and a task can be cancelled more than once inside it
(a ``wait_for`` timeout, then a shutdown drain). These tests cancel at each
await in that window — the increment, the terminal tail's job read, and the
step-progress write that used to sit between them — and assert the commit
lands and the finalize is enqueued every time.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _Gate:
    """A one-shot gate: a worker thread parks in it until the test opens it.

    Constructed ``open=True`` it never blocks, which is how a test lets the
    calls it is not cancelling at run straight through.
    """

    def __init__(self, *, open: bool = False) -> None:  # noqa: A002
        self.entered = threading.Event()
        self.release = threading.Event()
        if open:
            self.release.set()

    def block(self) -> None:
        """Called on the worker thread: announce, then wait to be released."""
        self.entered.set()
        assert self.release.wait(timeout=5), "gate was never released"


def _gated_adapter(
    commits: list[str],
    *,
    is_terminal: bool,
    increment_gate: _Gate,
    job_gate: _Gate,
    step_gate: _Gate,
) -> MagicMock:
    """Adapter whose blocking calls park in their gates on the worker thread.

    The increment records its commit *after* the block, mirroring the real
    method: the COMMIT lands even though the awaiting coroutine may already
    have been cancelled.
    """
    adapter = MagicMock()

    def _increment(**_: Any) -> dict[str, Any]:
        increment_gate.block()
        commits.append("increment")
        return {"completed": 3, "failed": 0, "total": 3, "is_terminal": is_terminal}

    def _get_job(*_: Any, **__: Any) -> dict[str, Any]:
        job_gate.block()
        return {"id": "job-1", "source_id": "src-1", "generate_embeddings": True}

    def _step_progress(*_: Any, **__: Any) -> None:
        step_gate.block()

    adapter.increment_job_completed_and_check = MagicMock(side_effect=_increment)
    adapter.update_step_progress = MagicMock(side_effect=_step_progress)
    adapter.get_extraction_job = MagicMock(side_effect=_get_job)
    return adapter


async def _wait_off_loop(event: threading.Event, what: str) -> None:
    """Await a ``threading.Event`` the worker thread sets, off the loop."""
    assert await asyncio.to_thread(event.wait, 5.0), f"timed out waiting for {what}"


async def _run_cancelled_at(
    adapter: MagicMock,
    mock_finalize: AsyncMock,
    cancels: Sequence[tuple[_Gate, str]],
) -> None:
    """Run ``_update_chunk_progress``, cancelling it at each named gate.

    Each entry waits until that adapter call is really running on its worker
    thread, cancels the task, lets the cancellation reach the coroutine, and
    only then releases the thread — so every cancel lands inside the window
    the shields have to survive. Several entries reproduce the multi-cancel
    sequence a ``wait_for`` timeout followed by a shutdown drain produces.
    """
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    settings = MagicMock()
    settings.priorities.background = 50

    service = ChunkExtractionOperationsService(source_repository=adapter)
    with patch.object(service, "queue_finalize_extraction", new=mock_finalize):
        task = asyncio.create_task(
            service._update_chunk_progress(
                adapter=adapter,
                job_id="job-1",
                source_id="src-1",
                database_name="default",
                chunk_task_id="t-1",
                chunk_index=0,
                task_outcome="completed",
                settings=settings,
            )
        )

        for gate, what in cancels:
            await _wait_off_loop(gate.entered, what)
            task.cancel()
            await asyncio.sleep(0)
            gate.release.set()

        with pytest.raises(asyncio.CancelledError):
            async with asyncio.timeout(5):
                await task


@pytest.mark.asyncio
async def test_cancel_mid_increment_still_enqueues_finalize_for_terminal_chunk() -> None:
    """The commit lands AND the terminal chunk's finalize enqueue is issued."""
    increment_gate = _Gate()
    commits: list[str] = []
    adapter = _gated_adapter(
        commits,
        is_terminal=True,
        increment_gate=increment_gate,
        job_gate=_Gate(open=True),
        step_gate=_Gate(open=True),
    )
    mock_finalize = AsyncMock(return_value="finalize-t")

    await _run_cancelled_at(adapter, mock_finalize, [(increment_gate, "the increment to start")])

    # (a) the increment's transaction really committed in its thread. The
    # coroutine cannot reach its re-raise before the shield loop has the
    # thread's result, so no extra synchronisation is needed here.
    assert commits == ["increment"]
    # (b) the progress it returned still drove the auto-finalize enqueue
    mock_finalize.assert_awaited_once()
    assert mock_finalize.await_args.kwargs["job_id"] == "job-1"
    assert mock_finalize.await_args.kwargs["source_id"] == "src-1"
    assert mock_finalize.await_args.kwargs["generate_embeddings"] is True
    # (c) the cosmetic step-progress write is deliberately skipped once a
    # cancellation is pending — it is not part of what the claim has to buy.
    adapter.update_step_progress.assert_not_called()


@pytest.mark.asyncio
async def test_second_cancel_during_the_finalize_tail_still_enqueues() -> None:
    """A cancel *after* the claim is made must not lose the enqueue either.

    Shielding only the increment leaves the tail that spends the claim — the
    ``get_extraction_job`` hop and ``queue_finalize_extraction`` — exposed:
    cancel #1 is absorbed by the increment's shield, cancel #2 lands on the
    job read, and the enqueue the claim paid for never happens.
    """
    increment_gate = _Gate()
    job_gate = _Gate()
    commits: list[str] = []
    adapter = _gated_adapter(
        commits,
        is_terminal=True,
        increment_gate=increment_gate,
        job_gate=job_gate,
        step_gate=_Gate(open=True),
    )
    mock_finalize = AsyncMock(return_value="finalize-t")

    await _run_cancelled_at(
        adapter,
        mock_finalize,
        [
            (increment_gate, "the increment to start"),
            (job_gate, "the finalize tail's job read to start"),
        ],
    )

    assert commits == ["increment"]
    mock_finalize.assert_awaited_once()
    assert mock_finalize.await_args.kwargs["job_id"] == "job-1"
    assert mock_finalize.await_args.kwargs["generate_embeddings"] is True


@pytest.mark.asyncio
async def test_first_cancel_on_the_step_progress_hop_still_enqueues() -> None:
    """The common case: no cancel during the increment, one just after it.

    The claim is already spent in the DB the moment the increment commits,
    so *no* unshielded await may sit between it and the enqueue. The
    cosmetic step-progress write is such an await — a single cancel landing
    on its thread hop used to propagate out before the terminal branch and
    lose the finalize, the same failure one await later.
    """
    step_gate = _Gate()
    commits: list[str] = []
    adapter = _gated_adapter(
        commits,
        is_terminal=True,
        increment_gate=_Gate(open=True),
        job_gate=_Gate(open=True),
        step_gate=step_gate,
    )
    mock_finalize = AsyncMock(return_value="finalize-t")

    await _run_cancelled_at(
        adapter, mock_finalize, [(step_gate, "the step-progress write to start")]
    )

    assert commits == ["increment"]
    mock_finalize.assert_awaited_once()
    assert mock_finalize.await_args.kwargs["job_id"] == "job-1"
    assert mock_finalize.await_args.kwargs["generate_embeddings"] is True


@pytest.mark.asyncio
async def test_cancel_mid_increment_enqueues_nothing_for_a_non_terminal_chunk() -> None:
    """Shielding must not invent a finalize the claim did not grant."""
    increment_gate = _Gate()
    commits: list[str] = []
    adapter = _gated_adapter(
        commits,
        is_terminal=False,
        increment_gate=increment_gate,
        job_gate=_Gate(open=True),
        step_gate=_Gate(open=True),
    )
    mock_finalize = AsyncMock(return_value="finalize-t")

    await _run_cancelled_at(adapter, mock_finalize, [(increment_gate, "the increment to start")])

    assert commits == ["increment"]
    mock_finalize.assert_not_awaited()
