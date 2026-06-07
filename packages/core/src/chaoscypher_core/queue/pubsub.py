# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""Valkey pub/sub helpers for real-time chat event delivery.

Provides publish and subscribe primitives for the chat background streaming
architecture. The worker publishes events to a per-chat channel, and the SSE
endpoint subscribes to relay them to the client.

Channel naming: ``chat:{chat_id}``

Example:
    # Publisher side (inside Neuron worker)
    from chaoscypher_core.queue import publish_chat_event

    success = await publish_chat_event(
        chat_id="abc123",
        event_type="token",
        data={"content": "Hello"},
    )

    # Subscriber side (inside SSE endpoint)
    from chaoscypher_core.queue import subscribe_chat_events

    async for event in subscribe_chat_events("abc123"):
        yield f"data: {json.dumps(event)}\\n\\n"

"""

import json
from collections.abc import AsyncIterator
from typing import Any

import structlog
from valkey.exceptions import TimeoutError as ValkeyTimeoutError

from chaoscypher_core.queue.client import queue_client


logger = structlog.get_logger(__name__)


async def publish_chat_event(chat_id: str, event_type: str, data: dict[str, Any]) -> bool:
    """Publish a single chat event to the per-chat Valkey channel.

    Serialises ``event_type`` and ``data`` as JSON and publishes to the channel
    ``chat:{chat_id}``.  Returns ``True`` on success and ``False`` if the Valkey
    client is unavailable or the publish call raises an exception.

    Args:
        chat_id: Unique identifier for the chat session.
        event_type: Event discriminator string (e.g. ``"token"``, ``"done"``).
        data: Arbitrary payload dict included under the ``"data"`` key.

    Returns:
        ``True`` if the event was published successfully, ``False`` otherwise.

    """
    client = queue_client.client
    if client is None:
        logger.warning("publish_chat_event_skipped", chat_id=chat_id, reason="client_unavailable")
        return False

    channel = f"chat:{chat_id}"
    payload = json.dumps({"type": event_type, "data": data})

    try:
        await client.publish(channel, payload)
        return True
    except Exception:
        logger.warning(
            "publish_chat_event_failed",
            chat_id=chat_id,
            event_type=event_type,
            exc_info=True,
        )
        return False


async def subscribe_chat_events(chat_id: str) -> AsyncIterator[dict[str, Any]]:
    """Subscribe to chat events for a given chat session.

    Creates a dedicated pub/sub connection, subscribes to ``chat:{chat_id}``,
    and yields parsed event dicts as they arrive.  Each yielded dict has the
    shape ``{"type": str, "data": dict}``.

    Handles ``ValkeyTimeoutError`` by re-entering the listen loop, since
    the pub/sub connection can be idle for extended periods while the LLM
    processes a request (tool execution, thinking, etc.).

    Properly unsubscribes and closes the pub/sub connection in a ``finally``
    block regardless of how the caller exits (normal return, exception, or
    ``GeneratorExit``).

    Args:
        chat_id: Unique identifier for the chat session to subscribe to.

    Yields:
        Parsed event dicts with ``"type"`` and ``"data"`` keys.

    """
    client = queue_client.client
    if client is None:
        logger.warning(
            "subscribe_chat_events_skipped", chat_id=chat_id, reason="client_unavailable"
        )
        return

    channel = f"chat:{chat_id}"
    pubsub = client.pubsub()

    # Allow up to 60 consecutive timeouts (~5s each ≈ 5 minutes) before
    # giving up. Each successful message resets the counter, so only
    # sustained silence (Valkey down or worker crashed) triggers the limit.
    max_consecutive_timeouts = 60
    consecutive_timeouts = 0

    try:
        await pubsub.subscribe(channel)
        logger.debug("chat_events_subscribed", chat_id=chat_id, channel=channel)

        while True:
            try:
                async for message in pubsub.listen():
                    if message is None:
                        continue

                    msg_type = message.get("type")
                    if msg_type != "message":
                        # Skip subscribe confirmation and other control messages
                        continue

                    raw_data = message.get("data")
                    if raw_data is None:
                        continue

                    # Valkey may return bytes or str depending on decode_responses setting
                    if isinstance(raw_data, bytes):
                        raw_data = raw_data.decode("utf-8")

                    try:
                        event = json.loads(raw_data)
                    except json.JSONDecodeError:
                        logger.warning(
                            "chat_event_decode_failed",
                            chat_id=chat_id,
                            raw=raw_data[:200] if isinstance(raw_data, str) else repr(raw_data),
                        )
                        continue

                    # Got a real message — reset timeout counter
                    consecutive_timeouts = 0
                    yield event

                # listen() exhausted normally — exit outer loop
                break

            except (ValkeyTimeoutError, TimeoutError):  # fmt: skip
                consecutive_timeouts += 1
                if consecutive_timeouts >= max_consecutive_timeouts:
                    logger.warning(
                        "chat_pubsub_max_timeouts",
                        chat_id=chat_id,
                        consecutive_timeouts=consecutive_timeouts,
                    )
                    raise
                # Pub/sub read timed out while waiting for the next message.
                # This is expected when the LLM is processing (tool calls,
                # thinking, etc.). Re-enter the listen loop to keep waiting.
                logger.debug(
                    "chat_pubsub_timeout_retry",
                    chat_id=chat_id,
                    attempt=consecutive_timeouts,
                )
                continue

    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception:
            logger.warning(
                "chat_pubsub_close_failed",
                chat_id=chat_id,
                exc_info=True,
            )
        logger.debug("chat_events_unsubscribed", chat_id=chat_id, channel=channel)
