# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat status recovery for crashed chat tasks.

When a worker crashes mid-chat-completion, the chat stays stuck in
``processing`` status forever because the handler's except block never
runs. This module provides a simple sweep that finds such chats and
moves them to ``error`` so the user sees a failure instead of an
infinite spinner.

Unlike source recovery, chat recovery does NOT re-dispatch work —
the user re-sends their message manually. The only goal is to make
the failure visible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.events.bus import event_bus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

logger = structlog.get_logger(__name__)

#: Chats stuck in "processing" longer than this are presumed crashed.
#: 5 minutes is generous — even a long tool-calling chat turn with
#: multiple LLM round-trips finishes well within this window.
DEFAULT_STUCK_THRESHOLD_SECONDS = 300


def reconcile_stuck_chats(
    adapter: SqliteAdapter,
    database_name: str,
    *,
    stuck_threshold_seconds: int = DEFAULT_STUCK_THRESHOLD_SECONDS,
) -> int:
    """Find chats stuck in 'processing' and move them to 'error'.

    Args:
        adapter: Storage adapter implementing ChatStorageProtocol.
        database_name: Database to scan.
        stuck_threshold_seconds: How long a chat can be "processing"
            before it's considered stuck.

    Returns:
        Number of chats recovered (moved to "error").

    """
    cutoff = datetime.now(UTC) - timedelta(seconds=stuck_threshold_seconds)
    processing_chats: list[dict[str, Any]] = adapter.list_chats(
        database_name,
        status="processing",
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
            try:
                adapter.update_chat(chat["id"], {"status": "error"})
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
