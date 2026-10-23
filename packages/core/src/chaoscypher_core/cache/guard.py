# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The cache never breaks a model call: store calls run off the event loop and fail soft.

A store call (a SQLite read or write, or opening the file) runs in a worker
thread so a writer waiting on another process's lock never stalls the
event loop; a store error (a locked-out write past the busy timeout, a full
disk, an unwritable data directory, a closed store) is logged
``llm_cache_store_error`` and the call proceeds as if the cache missed.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from typing import Any

import structlog


logger = structlog.get_logger(__name__)

STORE_ERRORS: tuple[type[BaseException], ...] = (sqlite3.Error, OSError)
"""What a store call may raise that the cache absorbs (anything else propagates)."""


async def store_call[T](fn: Callable[..., T], *args: Any, default: T, op: str) -> T:
    """``fn(*args)`` in a worker thread, or ``default`` (logged) when the store fails."""
    try:
        return await asyncio.to_thread(fn, *args)
    except STORE_ERRORS as exc:
        logger.warning(
            "llm_cache_store_error", op=op, error_type=type(exc).__name__, error=str(exc)[:200]
        )
        return default


__all__ = ["STORE_ERRORS", "store_call"]
