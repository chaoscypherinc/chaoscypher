# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``cancelled_by_signal``: SIGTERM or Ctrl-C cancels the block once, and its cleanup runs whole.

The signals are sent to this process; the block's handlers take them (on the main thread's
loop, where pytest runs the test), so the test runner itself is never stopped.
"""

from __future__ import annotations

import asyncio
import os
import signal

import pytest

from chaoscypher_cli.utils.signals import cancelled_by_signal


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
async def test_a_signal_cancels_the_block_and_the_block_ends_quietly(signum: int) -> None:
    async with cancelled_by_signal() as stop:
        os.kill(os.getpid(), signum)
        await asyncio.sleep(5)
        pytest.fail("the signal did not cancel the block")
    assert stop.signal == signum
    assert stop.exit_code == 128 + signum
    task = asyncio.current_task()
    assert task is not None and task.cancelling() == 0


async def test_a_second_sigterm_during_cleanup_does_not_cut_it_short() -> None:
    released: list[bool] = []
    async with cancelled_by_signal() as stop:
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.sleep(5)
        finally:
            os.kill(os.getpid(), signal.SIGTERM)  # the impatient second press
            await asyncio.sleep(0.2)  # the linker giving its claims back
            released.append(True)
    assert released == [True]
    assert stop.signal == signal.SIGTERM
    task = asyncio.current_task()
    assert task is not None and task.cancelling() == 0


async def test_a_block_with_no_signal_reports_none() -> None:
    async with cancelled_by_signal() as stop:
        await asyncio.sleep(0)
    assert stop.signal is None
    assert stop.exit_code is None
