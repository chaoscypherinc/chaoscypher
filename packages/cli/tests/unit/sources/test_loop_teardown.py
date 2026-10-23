# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A persistent loop's teardown is loop-safe: a pipeline error surfaces as itself (2026-10-08).

The benchmark runs the pipeline in a worker thread (the service's persistent
loop runs there) and leaves ``CLISourceProcessingService`` on its own running
loop. When finalization failed with a ``RecursionError`` and left tasks
pending, ``close()`` gathered them under the benchmark's loop (Python 3.14's
``gather`` binds to the loop running in the calling thread), raised
``ValueError: The future belongs to a different loop``, and that error
replaced the finalization's. The chat session's teardown had the same copy.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_cli.commands.chat import _close_loop
from chaoscypher_cli.sources.service import CLISourceProcessingService
from chaoscypher_cli.utils.event_loops import cancel_pending_tasks, run_to_completion


async def _fail_leaving_a_task(left: dict[str, Any]) -> None:
    """A finalization that fails with a task of its loop still pending."""
    left["task"] = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
    await asyncio.sleep(0)
    raise RecursionError("maximum recursion depth exceeded")


@pytest.mark.asyncio
async def test_a_failed_finalization_surfaces_as_itself_under_the_benchmark() -> None:
    """The benchmark's shape: pipeline in a worker thread, the service left on a running loop."""
    left: dict[str, Any] = {}
    with pytest.raises(RecursionError, match="maximum recursion depth"):
        with CLISourceProcessingService(MagicMock()) as service:
            await asyncio.to_thread(service._run_async, _fail_leaving_a_task(left))
    assert left["task"].cancelled()
    assert service._loop is None


@pytest.mark.asyncio
async def test_the_chat_loop_closes_from_inside_a_running_loop() -> None:
    """The chat session's teardown, called where another loop runs: its task is cancelled."""
    loop = asyncio.new_event_loop()
    left: dict[str, Any] = {}
    with pytest.raises(RecursionError):
        await asyncio.to_thread(loop.run_until_complete, _fail_leaving_a_task(left))
    _close_loop(loop)
    assert left["task"].cancelled()
    assert loop.is_closed()


def test_teardown_with_no_running_loop_cancels_and_drains() -> None:
    """The plain CLI shape (no loop running in this thread) still cancels and waits."""
    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(asyncio.sleep(3600))
        cancel_pending_tasks(loop)
        assert task.cancelled()
        assert run_to_completion(loop, asyncio.sleep(0, result=7)) == 7
    finally:
        loop.close()
    cancel_pending_tasks(loop)  # a closed loop has nothing to cancel
