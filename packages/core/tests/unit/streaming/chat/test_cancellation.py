# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the cross-process chat cancel flag.

The flag is the stop/cancel transport: POST /chats/{id}/cancel (cortex)
sets it, the worker's ``cancel_check`` dep reads it at loop step
boundaries, and the worker clears it at turn start. Reads must fail OPEN
(False) — a broken Valkey keeps the turn running, never kills it.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.streaming.chat.cancellation import (
    clear_cancel,
    is_cancel_requested,
    request_cancel,
)


class _FakeValkey:
    """In-memory async stand-in for the Valkey client."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class _BrokenValkey:
    """Client whose every call raises (transport down)."""

    async def set(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("valkey down")

    async def get(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("valkey down")

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("valkey down")


@pytest.mark.asyncio
async def test_request_then_is_requested_roundtrip() -> None:
    client = _FakeValkey()
    assert await request_cancel("c1", client=client) is True
    assert await is_cancel_requested("c1", client=client) is True
    assert "chat:cancel:c1" in client.store
    # The flag self-expires so an unconsumed cancel can't leak into a
    # later turn even if the explicit clear is missed.
    assert client.ttls["chat:cancel:c1"] > 0


@pytest.mark.asyncio
async def test_not_requested_by_default() -> None:
    assert await is_cancel_requested("c1", client=_FakeValkey()) is False


@pytest.mark.asyncio
async def test_flags_are_per_chat() -> None:
    client = _FakeValkey()
    await request_cancel("c1", client=client)
    assert await is_cancel_requested("other-chat", client=client) is False


@pytest.mark.asyncio
async def test_clear_removes_flag() -> None:
    client = _FakeValkey()
    await request_cancel("c1", client=client)
    await clear_cancel("c1", client=client)
    assert await is_cancel_requested("c1", client=client) is False


@pytest.mark.asyncio
async def test_decodes_bytes_values() -> None:
    """Valkey clients may return bytes; the flag still reads as set."""
    client = _FakeValkey()
    client.store["chat:cancel:c1"] = b"1"  # type: ignore[assignment]
    assert await is_cancel_requested("c1", client=client) is True


@pytest.mark.asyncio
async def test_request_cancel_reports_transport_failure() -> None:
    assert await request_cancel("c1", client=_BrokenValkey()) is False


@pytest.mark.asyncio
async def test_is_requested_fails_open_to_false() -> None:
    """A broken transport must keep the turn RUNNING, never kill it."""
    assert await is_cancel_requested("c1", client=_BrokenValkey()) is False


@pytest.mark.asyncio
async def test_clear_swallows_transport_failure() -> None:
    await clear_cancel("c1", client=_BrokenValkey())  # must not raise


@pytest.mark.asyncio
async def test_missing_client_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """No connected Valkey client: request reports False, read fails open."""
    from chaoscypher_core.streaming.chat import cancellation

    monkeypatch.setattr(cancellation, "_default_client", lambda: None)
    assert await request_cancel("c1") is False
    assert await is_cancel_requested("c1") is False
    await clear_cancel("c1")  # must not raise
