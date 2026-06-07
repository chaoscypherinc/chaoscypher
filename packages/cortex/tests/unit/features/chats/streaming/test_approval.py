# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the pending-approval store."""

from __future__ import annotations

import asyncio

import pytest

from chaoscypher_core.streaming.chat.approval import (
    PendingApprovalStore,
)


@pytest.mark.asyncio
async def test_create_and_resolve_roundtrip() -> None:
    """Creating a pending approval and resolving it wakes the waiter."""
    store = PendingApprovalStore()
    entry = await store.create(
        chat_id="chat-1",
        tool_call_id="call-1",
        tool_name="create_node",
        arguments={"label": "Alice"},
    )
    assert entry.tool_call_id == "call-1"
    assert entry.tool_name == "create_node"
    assert entry.arguments == {"label": "Alice"}

    # Concurrent waiter + resolver.
    async def resolver() -> None:
        # Small delay to ensure the waiter is already awaiting.
        await asyncio.sleep(0)
        resolved = await store.resolve("chat-1", "call-1", "approve")
        assert resolved is True

    decision, _ = await asyncio.gather(entry.wait(timeout_seconds=1.0), resolver())
    assert decision == "approve"


@pytest.mark.asyncio
async def test_create_duplicate_raises() -> None:
    """Creating a second entry with the same key raises ValueError."""
    store = PendingApprovalStore()
    await store.create("chat-1", "call-1", "t", {})
    with pytest.raises(ValueError, match="Duplicate pending approval"):
        await store.create("chat-1", "call-1", "t", {})


@pytest.mark.asyncio
async def test_wait_returns_timeout() -> None:
    """If no one resolves within the timeout, wait() returns 'timeout'."""
    store = PendingApprovalStore()
    entry = await store.create("chat-1", "call-1", "t", {})
    decision = await entry.wait(timeout_seconds=0.05)
    assert decision == "timeout"


@pytest.mark.asyncio
async def test_resolve_missing_returns_false() -> None:
    """Resolving a key with no registered entry returns False."""
    store = PendingApprovalStore()
    resolved = await store.resolve("chat-1", "call-ghost", "approve")
    assert resolved is False


@pytest.mark.asyncio
async def test_cleanup_is_idempotent() -> None:
    """Cleanup removes entries and is safe to call repeatedly."""
    store = PendingApprovalStore()
    await store.create("chat-1", "call-1", "t", {})

    await store.cleanup("chat-1", "call-1")
    # Second cleanup must not raise.
    await store.cleanup("chat-1", "call-1")
    # Cleanup of an entry that never existed is also a no-op.
    await store.cleanup("chat-2", "call-missing")

    # After cleanup, resolve should report False (no entry).
    resolved = await store.resolve("chat-1", "call-1", "approve")
    assert resolved is False


@pytest.mark.asyncio
async def test_reject_decision_roundtrip() -> None:
    """'reject' flows through wait() unchanged."""
    store = PendingApprovalStore()
    entry = await store.create("chat-1", "call-1", "delete_node", {"id": "n1"})

    async def resolver() -> None:
        await asyncio.sleep(0)
        await store.resolve("chat-1", "call-1", "reject")

    decision, _ = await asyncio.gather(entry.wait(timeout_seconds=1.0), resolver())
    assert decision == "reject"
