# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the pending-approval store used by chat tool calls.

Covers ``PendingApproval.wait`` (approve / reject / timeout / default-reject)
and ``PendingApprovalStore`` create/resolve/cleanup semantics. Pure in-memory
asyncio — no provider, settings, or DB wiring required.
"""

from __future__ import annotations

import asyncio

import pytest

from chaoscypher_core.streaming.chat.approval import (
    PendingApproval,
    PendingApprovalStore,
)


@pytest.mark.asyncio
async def test_wait_returns_approve_when_resolved_in_time() -> None:
    """A resolve('approve') before the timeout wakes the waiter with 'approve'."""
    entry = PendingApproval(tool_call_id="c1", tool_name="t", arguments={})

    async def _resolver() -> None:
        await asyncio.sleep(0)  # yield so the waiter is parked first
        entry.resolve("approve")

    resolver = asyncio.create_task(_resolver())
    decision = await entry.wait(timeout_seconds=1.0)
    await resolver

    assert decision == "approve"


@pytest.mark.asyncio
async def test_wait_returns_reject_when_resolved_reject() -> None:
    """resolve('reject') propagates as 'reject'."""
    entry = PendingApproval(tool_call_id="c1", tool_name="t", arguments={})
    entry.resolve("reject")  # pre-resolve: event already set

    decision = await entry.wait(timeout_seconds=1.0)

    assert decision == "reject"


@pytest.mark.asyncio
async def test_wait_times_out_when_never_resolved() -> None:
    """No resolve before the sub-second timeout yields 'timeout'."""
    entry = PendingApproval(tool_call_id="c1", tool_name="t", arguments={})

    decision = await entry.wait(timeout_seconds=0.01)

    assert decision == "timeout"


@pytest.mark.asyncio
async def test_wait_defaults_to_reject_when_resolved_without_decision() -> None:
    """Fail-closed: if the event is set but no decision was stored, default 'reject'."""
    entry = PendingApproval(tool_call_id="c1", tool_name="t", arguments={})
    # Wake the waiter directly without going through resolve() (decision stays None).
    entry._event.set()

    decision = await entry.wait(timeout_seconds=1.0)

    assert decision == "reject"


@pytest.mark.asyncio
async def test_store_create_then_resolve_wakes_waiter() -> None:
    """create() registers an entry; resolve() on the store wakes its waiter."""
    store = PendingApprovalStore()
    entry = await store.create(
        chat_id="chat-1",
        tool_call_id="tc-1",
        tool_name="delete_node",
        arguments={"node_id": "n1"},
    )

    async def _resolver() -> None:
        await asyncio.sleep(0)
        found = await store.resolve("chat-1", "tc-1", "approve")
        assert found is True

    resolver = asyncio.create_task(_resolver())
    decision = await entry.wait(timeout_seconds=1.0)
    await resolver

    assert decision == "approve"


@pytest.mark.asyncio
async def test_store_create_duplicate_raises_value_error() -> None:
    """A second create() for the same (chat_id, tool_call_id) raises ValueError."""
    store = PendingApprovalStore()
    await store.create(chat_id="chat-1", tool_call_id="tc-1", tool_name="t", arguments={})

    with pytest.raises(ValueError, match="Duplicate pending approval"):
        await store.create(chat_id="chat-1", tool_call_id="tc-1", tool_name="t", arguments={})


@pytest.mark.asyncio
async def test_store_resolve_miss_returns_false() -> None:
    """resolve() for an unknown key returns False (already-resolved / never created)."""
    store = PendingApprovalStore()

    result = await store.resolve("chat-x", "tc-x", "approve")

    assert result is False


@pytest.mark.asyncio
async def test_store_cleanup_is_idempotent() -> None:
    """cleanup() removes the entry and is safe to call repeatedly."""
    store = PendingApprovalStore()
    await store.create(chat_id="chat-1", tool_call_id="tc-1", tool_name="t", arguments={})

    await store.cleanup("chat-1", "tc-1")
    # Second cleanup must not raise.
    await store.cleanup("chat-1", "tc-1")

    # After cleanup the entry is gone, so a resolve misses.
    assert await store.resolve("chat-1", "tc-1", "approve") is False
