# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""subscribe_chat_events must yield the ``__subscribed__`` sentinel first.

The SSE endpoint reads chat status BEFORE subscribing; a terminal event
published in that window is delivered to nobody. The sentinel — emitted
immediately after SUBSCRIBE completes — is the consumer's cue to re-check
the status it read pre-subscribe (2026-08-03 audit).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.queue import pubsub as pubsub_module


class _FakePubSub:
    """Minimal stand-in for valkey's async PubSub object."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages
        self.subscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.subscribed.remove(channel)

    async def aclose(self) -> None:
        self.closed = True

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        for message in self._messages:
            yield message


def _patch_queue_client(monkeypatch: pytest.MonkeyPatch, fake_pubsub: _FakePubSub) -> None:
    fake_client = MagicMock()
    fake_client.pubsub.return_value = fake_pubsub
    fake_queue_client = MagicMock()
    fake_queue_client.client = fake_client
    monkeypatch.setattr(pubsub_module, "queue_client", fake_queue_client)


@pytest.mark.asyncio
async def test_sentinel_is_yielded_first_then_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({"type": "done", "data": {"status": "completed"}})
    fake_pubsub = _FakePubSub(
        [
            {"type": "subscribe", "data": 1},  # control message, skipped
            {"type": "message", "data": payload},
        ]
    )
    _patch_queue_client(monkeypatch, fake_pubsub)

    events = [e async for e in pubsub_module.subscribe_chat_events("c1")]

    assert events[0] == {"type": "__subscribed__", "data": {}}
    assert events[1] == {"type": "done", "data": {"status": "completed"}}
    assert fake_pubsub.closed is True


@pytest.mark.asyncio
async def test_sentinel_emitted_even_when_channel_stays_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sentinel arrives after SUBSCRIBE, before any published message."""
    fake_pubsub = _FakePubSub([])
    _patch_queue_client(monkeypatch, fake_pubsub)

    events = [e async for e in pubsub_module.subscribe_chat_events("c1")]

    assert events == [{"type": "__subscribed__", "data": {}}]
