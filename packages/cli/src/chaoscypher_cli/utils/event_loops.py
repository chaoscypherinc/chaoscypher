# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Event-loop teardown that is safe from any thread, a running loop or none.

The CLI keeps a persistent event loop per service or session and drives it
with ``run_until_complete``. Its teardown cancels the loop's pending tasks
and waits for them, and that wait must run on that loop: since Python 3.14,
``asyncio.gather`` binds its outer future to the loop *running in the calling
thread*, so gathering a persistent loop's tasks from a thread that runs
another loop raises ``ValueError: The future belongs to a different loop``.
The benchmark does exactly that (it closes ``CLISourceProcessingService`` on
its own loop after running the pipeline in a worker thread): on 2026-10-08 a
finalization ``RecursionError`` surfaced as that ``ValueError`` instead.

Example:
    from chaoscypher_cli.utils.event_loops import cancel_pending_tasks, run_to_completion

    cancel_pending_tasks(loop)
    run_to_completion(loop, loop.shutdown_asyncgens())
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def run_to_completion[T](loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` on ``loop`` (which must not be running) from any thread.

    A thread may run one loop at a time, so when this thread already runs
    another loop, ``loop`` is driven on a short-lived helper thread and the
    call waits for it.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return loop.run_until_complete(coro)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="loop-teardown") as pool:
        return pool.submit(loop.run_until_complete, coro).result()


def cancel_pending_tasks(loop: asyncio.AbstractEventLoop) -> None:
    """Cancel every pending task of ``loop`` and wait, on ``loop``, until each has finished.

    A closed loop has nothing to cancel. A loop running in another thread is
    asked to cancel its tasks there (it finishes them itself); waiting on it
    from here would raise.
    """
    if loop.is_closed():
        return
    pending = asyncio.all_tasks(loop)
    if not pending:
        return
    if loop.is_running():
        for task in pending:
            loop.call_soon_threadsafe(task.cancel)
        return
    for task in pending:
        task.cancel()

    async def _drain() -> None:
        """Wait for every cancelled task to finish, swallowing its CancelledError."""
        await asyncio.gather(*pending, return_exceptions=True)

    run_to_completion(loop, _drain())
