# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cross-process cancel flag for in-flight chat turns.

``POST /chats/{chat_id}/cancel`` (cortex) sets a short-lived Valkey key;
the shared chat tool loop (running in the neuron worker) polls it at step
boundaries through the worker's ``cancel_check`` dep. Reads fail OPEN
(False) — a broken transport keeps the turn running rather than killing
it. The flag self-expires so an unconsumed cancel cannot leak into a
later turn, and the worker also clears it explicitly at turn start.
"""

from typing import Any

from chaoscypher_core.utils.logging.app_config import get_logger


logger = get_logger(__name__)

# Generous upper bound on a turn's lifetime; the worker's clear-at-start
# makes correctness independent of this, the TTL is just leak hygiene.
_CANCEL_TTL_SECONDS = 600


def _cancel_key(chat_id: str) -> str:
    """Valkey key carrying one chat's cancel request."""
    return f"chat:cancel:{chat_id}"


def _default_client() -> Any:
    """Return the process-wide Valkey client (lazy import avoids cycles)."""
    from chaoscypher_core.queue.client import queue_client

    return queue_client.client


async def request_cancel(chat_id: str, client: Any = None) -> bool:
    """Flag the chat's in-flight turn for cancellation.

    Args:
        chat_id: Chat whose running turn should stop.
        client: Async Valkey client override (tests); defaults to the
            shared queue client.

    Returns:
        True when the flag was recorded; False when the transport is
        unavailable or errored (the endpoint surfaces that as a 503).

    """
    client = client if client is not None else _default_client()
    if client is None:
        logger.warning("chat_cancel_request_skipped", chat_id=chat_id, reason="valkey_unavailable")
        return False
    try:
        await client.set(_cancel_key(chat_id), "1", ex=_CANCEL_TTL_SECONDS)
    except Exception:
        logger.exception("chat_cancel_request_failed", chat_id=chat_id)
        return False
    logger.info("chat_cancel_requested", chat_id=chat_id)
    return True


async def is_cancel_requested(chat_id: str, client: Any = None) -> bool:
    """Check the cancel flag (fail-open: errors read as not-cancelled).

    Args:
        chat_id: Chat whose flag to read.
        client: Async Valkey client override (tests); defaults to the
            shared queue client.

    Returns:
        True only when a cancel flag is present and readable.

    """
    client = client if client is not None else _default_client()
    if client is None:
        return False
    try:
        value = await client.get(_cancel_key(chat_id))
    except Exception:
        logger.warning("chat_cancel_check_failed", chat_id=chat_id)
        return False
    return value is not None


async def clear_cancel(chat_id: str, client: Any = None) -> None:
    """Remove the cancel flag (worker calls this at turn start).

    Best-effort: transport errors are swallowed — the TTL bounds any
    leftover flag, and a fresh turn clears again before it could matter.

    Args:
        chat_id: Chat whose flag to clear.
        client: Async Valkey client override (tests); defaults to the
            shared queue client.

    """
    client = client if client is not None else _default_client()
    if client is None:
        return
    try:
        await client.delete(_cancel_key(chat_id))
    except Exception:
        logger.warning("chat_cancel_clear_failed", chat_id=chat_id)


__all__ = [
    "clear_cancel",
    "is_cancel_requested",
    "request_cancel",
]
