# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: the commit-phase handoff must offload its blocking SQLite I/O.

``_complete_finalization`` ends with
``await asyncio.to_thread(_run_complete_finalize_txn)`` and then, two lines
later, calls ``_queue_commit_phase``. That helper used to run its full-row
``adapter.get_file`` on the event loop, and ``queue_import_commit`` used to
run ``adapter.set_source_commit_payload`` — a ``json.dumps`` of the entire
matched-entity/relationship set plus a TEXT ``UPDATE`` + ``COMMIT``, the
single biggest write of the whole finalizer — on the event loop too. Any
``SafeSession._retry_delay`` ``time.sleep`` during SQLITE_BUSY contention
therefore froze every other slot of the 8-concurrent Operations queue.

Both are now wrapped in ``asyncio.to_thread``, matching the four sibling
transaction bodies in ``extraction_finalizer.py`` (2026-05-23 perf fix).
The offload is orthogonal to the 2026-05-20 hoist that keeps the enqueue
outside ``adapter.transaction()``; ``test_finalize_transaction_integrity``
pins that property and must stay green.

Thread placement and loop liveness are asserted directly — no timing
thresholds (see ``test_handler_transaction_thread_offload.py``).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.auto_enable = True
    settings.priorities.background = 50
    return settings


@pytest.mark.asyncio
async def test_queue_commit_phase_get_file_runs_on_a_worker_thread() -> None:
    """``_queue_commit_phase``'s full-row read must execute off the loop thread.

    If the ``asyncio.to_thread`` wrapper around ``adapter.get_file`` is
    replaced with a direct call, the read runs on the event loop's own
    thread and the thread-identity assertion fails.
    """
    from chaoscypher_core.operations.extraction.extraction_finalizer import (
        _queue_commit_phase,
    )

    observed: dict[str, Any] = {}

    def get_file(source_id: str, database_name: str) -> dict[str, Any]:
        observed["thread_id"] = threading.get_ident()
        return {"id": source_id, "filename": "test.txt"}

    adapter = MagicMock()
    adapter.get_file = get_file

    with patch(
        "chaoscypher_core.operations.queue_utils.queue_import_commit",
        new=AsyncMock(return_value="task-1"),
    ):
        await _queue_commit_phase(
            adapter,
            "src-1",
            "default",
            [{"id": "e1"}],
            [{"id": "r1"}],
            {"suggested_templates": []},
            _settings(),
        )

    assert observed["thread_id"] != threading.get_ident(), (
        "adapter.get_file ran on the event-loop thread — the asyncio.to_thread "
        "offload in _queue_commit_phase has regressed"
    )


@pytest.mark.asyncio
async def test_commit_payload_write_runs_on_a_worker_thread() -> None:
    """``queue_import_commit`` must persist the MB-scale payload off the loop.

    ``set_source_commit_payload`` json-dumps the whole commit_data dict into
    a TEXT column and commits. Running it on the loop is the largest inline
    write on the finalizer's happy path.
    """
    from chaoscypher_core.operations import queue_utils

    observed: dict[str, Any] = {}

    def set_source_commit_payload(
        file_id: str, payload: dict[str, Any], database_name: str
    ) -> None:
        observed["thread_id"] = threading.get_ident()
        observed["payload"] = payload

    adapter = MagicMock()
    adapter.set_source_commit_payload = set_source_commit_payload

    with patch.object(
        queue_utils.queue_client, "enqueue_task", new=AsyncMock(return_value="task-1")
    ):
        task_id = await queue_utils.queue_import_commit(
            file_id="src-1",
            commit_data={"entities": [{"id": "e1"}], "relationships": []},
            file_info={"id": "src-1"},
            adapter=adapter,
            database_name="default",
        )

    assert task_id == "task-1"
    assert observed["payload"]["entities"] == [{"id": "e1"}]
    assert observed["thread_id"] != threading.get_ident(), (
        "set_source_commit_payload ran on the event-loop thread — the "
        "asyncio.to_thread offload in queue_import_commit has regressed"
    )


@pytest.mark.asyncio
async def test_event_loop_stays_live_while_commit_payload_write_blocks() -> None:
    """The loop must keep running while the offloaded payload write blocks.

    The fake ``set_source_commit_payload`` blocks until a coroutine running
    on the event loop releases it. With the offload intact that release is
    scheduled while the worker thread waits; if the write runs on the loop,
    the releasing coroutine can never run and the ``wait`` below times out —
    a deadline, not a timing threshold.
    """
    from chaoscypher_core.operations import queue_utils

    release = threading.Event()
    entered: asyncio.Event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def set_source_commit_payload(
        file_id: str, payload: dict[str, Any], database_name: str
    ) -> None:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=5), (
            "event loop never released the blocked write — "
            "set_source_commit_payload is starving the loop it was supposed "
            "to be offloaded from"
        )

    adapter = MagicMock()
    adapter.set_source_commit_payload = set_source_commit_payload

    with patch.object(
        queue_utils.queue_client, "enqueue_task", new=AsyncMock(return_value="task-1")
    ):
        async with asyncio.timeout(10):
            task = asyncio.create_task(
                queue_utils.queue_import_commit(
                    file_id="src-1",
                    commit_data={"entities": []},
                    file_info={"id": "src-1"},
                    adapter=adapter,
                    database_name="default",
                )
            )
            await entered.wait()  # the write has started on its worker thread
            release.set()  # only reachable while the event loop is responsive
            await task
