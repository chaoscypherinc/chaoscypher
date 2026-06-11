# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Event sinks for the shared chat tool loop.

The loop emits transport-agnostic events; these sinks deliver them. The
worker uses :class:`ValkeyPubSubSink` (per-chat pub/sub channel relayed to
the browser by GET /chats/{id}/events); tests use :class:`CollectingSink`.
The CLI's console-rendering sink lives in the CLI package (it depends on
rich, which core must not import).
"""

from collections.abc import Awaitable, Callable
from typing import Any

from chaoscypher_core.utils.logging.app_config import get_logger


logger = get_logger(__name__)

PublishFn = Callable[[str, str, dict[str, Any]], Awaitable[Any]]


class ValkeyPubSubSink:
    """Delivers loop events to the chat's Valkey pub/sub channel.

    The publish function is injected (rather than imported here) so the
    host process controls — and tests can patch — the actual transport.
    Delivery is best-effort: a publish failure is logged and swallowed,
    never breaking the chat turn.
    """

    def __init__(self, chat_id: str, publish: PublishFn) -> None:
        """Bind the sink to one chat channel.

        Args:
            chat_id: Channel key.
            publish: ``publish_chat_event``-compatible callable.

        """
        self._chat_id = chat_id
        self._publish = publish

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish one event; failures are logged, never raised."""
        try:
            await self._publish(self._chat_id, event_type, payload)
        except Exception:
            logger.warning(
                "chat_event_publish_failed",
                chat_id=self._chat_id,
                event_type=event_type,
                exc_info=True,
            )


class CollectingSink:
    """Test sink: records every emitted event as ``(event_type, payload)``."""

    def __init__(self) -> None:
        """Start with an empty event log."""
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Append the event to the log."""
        self.events.append((event_type, payload))

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        """All payloads emitted with the given event type, in order."""
        return [p for t, p in self.events if t == event_type]


__all__ = ["CollectingSink", "PublishFn", "ValkeyPubSubSink"]
