# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared done-callbacks for ``asyncio.Task`` background work.

Cortex (lifespan) and Neuron (worker) both spawn long-running background
tasks via ``asyncio.create_task`` and need a consistent way to surface
exceptions instead of letting them disappear silently. This module
provides the canonical callback used in both places.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    import asyncio


__all__ = ["log_task_exception"]


logger = structlog.get_logger(__name__)


def log_task_exception(task: asyncio.Task[Any]) -> None:
    """Surface background-task failures.

    Attach via ``task.add_done_callback`` to every ``asyncio.create_task``
    call. Cancellation is normal during shutdown and is silently ignored;
    any other exception is logged at ERROR with the task's name so a
    pre-cancellation crash leaves a trace instead of vanishing.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    logger.error(
        "background_task_failed",
        task_name=task.get_name(),
        error_type=type(exc).__name__,
        error_message=str(exc),
        exc_info=exc,
    )
