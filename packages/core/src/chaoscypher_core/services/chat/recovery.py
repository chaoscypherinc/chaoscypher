# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat status recovery for crashed chat tasks.

When a worker crashes mid-chat-completion, the chat stays stuck in
``processing`` status forever because the handler's except block never
runs. This module provides a simple sweep that finds such chats and
moves them to ``error`` so the user sees a failure instead of an
infinite spinner.

``chat.updated_at`` is never bumped mid-turn (no ``onupdate``, and the
loop's only persistence point is at the very end of a successful run),
so a stale timestamp alone cannot distinguish a healthy long-running
turn from a genuinely crashed one — the worker gets up to 3600s per
turn. Before declaring a chat dead, the sweep consults the queue for a
live ``chat_background`` task backing that chat's turn (the worker
already maintains a heartbeat for it); the timestamp check is only the
final word when there's no queue evidence either way (no queue_client
wired, or a genuinely absent task).

Unlike source recovery, chat recovery does NOT re-dispatch work —
the user re-sends their message manually. The only goal is to make
the failure visible.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.events.bus import event_bus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

logger = structlog.get_logger(__name__)

#: Chats stuck in "processing" longer than this are a *candidate* for
#: recovery — NOT proof of death. A turn's own worker budget is 3600s
#: (see ``llm_worker_default`` / the "llm" queue timeout), so plenty of
#: healthy long-running turns cross this threshold; ``reconcile_stuck_chats``
#: only actually flips a candidate once the queue also confirms no live
#: task backs it (or there's no queue to ask).
DEFAULT_STUCK_THRESHOLD_SECONDS = 300


async def _worker_alive_for_chat(
    queue_client: Any,
    *,
    chat_id: str,
    database_name: str,
) -> bool:
    """Whether a live queue task backs this chat's in-flight turn.

    Encodes the sweep's fail-safe policy in one place: an unavailable or
    erroring queue must never cause a healthy chat to be flipped to
    "error" (that flip-flop — sweeper writes "error", the worker's own
    completion path later overwrites it with "active", and a user retry
    in between double-enqueues the turn — is exactly the harm entry 812
    describes). A missing ``queue_client`` (no queue wired at all) is
    treated as "no evidence" rather than "alive", so the caller falls
    back to the plain timestamp check.

    Args:
        queue_client: Duck-typed queue client exposing
            ``task_exists_for_chat(chat_id=..., database_name=...)``
            (see ``chaoscypher_core.queue.client.QueueClient``); ``None``
            when no queue backend is configured for this process.
        chat_id: Chat whose in-flight turn to check.
        database_name: Database scope for the chat.

    Returns:
        One of three outcomes:

        - ``True`` — a live (queued/running) ``chat_background`` task
          exists for this chat, OR ``task_exists_for_chat`` raised
          (including ``QueueUnavailableError`` when the queue backend is
          configured but not connected — a "we don't know" state that
          must never read as "safe to flip", not just a transient scan
          hiccup).
        - ``False`` (queue was consulted) — the queue is connected,
          reachable, and genuinely has no matching task.
        - ``False`` (queue was NOT consulted) — ``queue_client`` itself is
          ``None`` (no queue wired into this process at all), so there is
          no evidence either way and the caller falls back to the plain
          timestamp check.
    """
    if queue_client is None:
        return False
    try:
        exists: bool = await queue_client.task_exists_for_chat(
            chat_id=chat_id,
            database_name=database_name,
        )
        return exists
    except Exception as exc:
        logger.warning(
            "chat_liveness_check_failed_assuming_alive",
            chat_id=chat_id,
            database_name=database_name,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return True


async def reconcile_stuck_chats(
    adapter: SqliteAdapter,
    database_name: str,
    *,
    stuck_threshold_seconds: int = DEFAULT_STUCK_THRESHOLD_SECONDS,
    queue_client: Any = None,
) -> int:
    """Find chats stuck in 'processing' and move them to 'error'.

    Args:
        adapter: Storage adapter implementing ChatStorageProtocol.
        database_name: Database to scan.
        stuck_threshold_seconds: How long a chat can be "processing"
            before it's a *candidate* for recovery — actual recovery
            additionally requires the queue liveness check (or the
            absence of a queue_client) to agree the chat is dead.
        queue_client: Duck-typed queue client exposing
            ``task_exists_for_chat`` (see
            ``chaoscypher_core.queue.client.QueueClient``), used to
            confirm a stuck candidate has no live worker task before
            flipping it. ``None`` (the default) skips the liveness
            check entirely and reproduces the pre-fix timestamp-only
            behavior — the correct fallback when no queue is wired
            (e.g. offline/CLI-only contexts).

    Returns:
        Number of chats recovered (moved to "error").

    """
    cutoff = datetime.now(UTC) - timedelta(seconds=stuck_threshold_seconds)
    # Explicit high limit: the protocol default (100, newest first) would
    # leave the OLDEST stuck chats — the ones most in need of recovery —
    # permanently outside the sweep when more than 100 are wedged.
    # Adapter calls are blocking SQLite I/O — keep them off the event loop.
    processing_chats: list[dict[str, Any]] = await asyncio.to_thread(
        adapter.list_chats,
        database_name,
        status="processing",
        limit=10_000,
    )

    recovered = 0
    for chat in processing_chats:
        updated_at = chat.get("updated_at")
        if updated_at is None:
            continue

        # updated_at may be a string or a datetime depending on the
        # adapter serialization path.
        if isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at)
            except ValueError:
                continue

        # Make timezone-aware if naive (SQLite stores without tz).
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)

        if updated_at < cutoff:
            if await _worker_alive_for_chat(
                queue_client,
                chat_id=chat["id"],
                database_name=database_name,
            ):
                logger.debug(
                    "chat_stuck_candidate_has_live_task",
                    chat_id=chat["id"],
                    database_name=database_name,
                )
                continue

            try:
                await asyncio.to_thread(adapter.update_chat, chat["id"], {"status": "error"})
                recovered += 1
                event_bus.emit(
                    "recovery",
                    action="Chat recovered from stuck 'processing' state",
                    source="reconciler",
                    details={"chat_id": chat["id"]},
                    database_name=database_name,
                )
                logger.warning(
                    "chat_recovered_from_stuck_processing",
                    chat_id=chat["id"],
                    database_name=database_name,
                    stuck_since=updated_at.isoformat(),
                )
            except Exception:
                logger.exception(
                    "chat_recovery_failed",
                    chat_id=chat["id"],
                    database_name=database_name,
                )

    return recovered
