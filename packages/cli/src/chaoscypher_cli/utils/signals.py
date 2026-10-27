# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SIGTERM and Ctrl-C end a long-running command with its cleanup run.

Python's default SIGTERM ends the process at once, so ``finally`` blocks never run; for the
memory commands that means a memory being linked keeps its claim until the claim goes
stale (5 minutes) instead of being given back. Inside :func:`cancelled_by_signal`, SIGTERM
or SIGINT cancels the task that entered it, once: the cancellation runs every ``finally``
(the memory linker's close gives each claim back), a second signal during that cleanup is
ignored, and the block ends quietly, telling the caller through :attr:`Termination.signal`.

Example:
    async with cancelled_by_signal() as stop:
        await serve()
    if stop.exit_code is not None:
        sys.exit(stop.exit_code)
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import AsyncIterator
from dataclasses import dataclass


STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)
"""The signals that stop a command: a service manager's SIGTERM, a person's Ctrl-C."""


@dataclass
class Termination:
    """Which signal stopped the block, if one did."""

    signal: int | None = None

    @property
    def exit_code(self) -> int | None:
        """The exit code for the signal (the shell's 128 + its number), None when none came."""
        return None if self.signal is None else 128 + self.signal


@contextlib.asynccontextmanager
async def cancelled_by_signal() -> AsyncIterator[Termination]:
    """While inside, SIGTERM or SIGINT cancels the current task once; the block then ends quietly.

    Where the loop cannot take a signal handler (Windows, or a loop off the main thread)
    the signals keep their defaults.
    """
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    stop = Termination()
    cancelled = False

    def _stop(signum: int) -> None:
        """The first signal: remember it, and cancel the block's task; later ones are ignored."""
        nonlocal cancelled
        if stop.signal is not None:
            return
        stop.signal = signum
        if task is not None:
            task.cancel()
            cancelled = True

    installed: list[int] = []
    for stop_signal in STOP_SIGNALS:
        signum = int(stop_signal)
        try:
            loop.add_signal_handler(signum, _stop, signum)
        except NotImplementedError, RuntimeError, ValueError:
            continue
        installed.append(signum)
    try:
        yield stop
    except asyncio.CancelledError:
        if stop.signal is None:
            raise
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)
        if cancelled and task is not None and task.cancelling():
            # ours, whether it was delivered or is still pending after the block finished
            task.uncancel()
