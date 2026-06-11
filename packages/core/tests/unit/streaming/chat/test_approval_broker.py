# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the Valkey-backed tool-approval broker.

The broker carries approval decisions across processes: the chat tool loop
(worker process) registers a pending key and polls it; the REST endpoint
(cortex process) flips the key to the user's decision. Fail-closed: no
decision within the timeout means denial.
"""

from __future__ import annotations

from chaoscypher_core.streaming.chat.approval_broker import (
    PENDING_SENTINEL,
    ValkeyApprovalBroker,
    _approval_key,
    resolve_tool_approval,
)


class _FakeValkey:
    """Minimal async key-value fake (get/set with ex/keepttl)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        keepttl: bool = False,
    ) -> None:
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


def _broker(fake: _FakeValkey) -> ValkeyApprovalBroker:
    return ValkeyApprovalBroker(client_getter=lambda: fake)


async def test_request_registers_pending_key_with_ttl() -> None:
    fake = _FakeValkey()
    await _broker(fake).request("c1", "tc-1", "create_node", {"label": "X"}, iteration=1)
    key = _approval_key("c1", "tc-1")
    assert fake.store[key] == PENDING_SENTINEL
    assert fake.ttls[key] > 0


async def test_wait_returns_decision_when_key_flips() -> None:
    fake = _FakeValkey()
    broker = _broker(fake)
    await broker.request("c1", "tc-1", "create_node", {}, iteration=1)
    # Decision arrives before the first poll completes.
    fake.store[_approval_key("c1", "tc-1")] = "approve"
    decision = await broker.wait("c1", "tc-1", timeout_s=5)
    assert decision == "approve"


async def test_wait_times_out_to_timeout_decision() -> None:
    fake = _FakeValkey()
    broker = _broker(fake)
    await broker.request("c1", "tc-2", "delete_node", {}, iteration=1)
    # Nobody answers; sub-second timeout for the test.
    decision = await broker.wait("c1", "tc-2", timeout_s=0.2)
    assert decision == "timeout"


async def test_wait_cleans_up_key_after_decision() -> None:
    fake = _FakeValkey()
    broker = _broker(fake)
    await broker.request("c1", "tc-3", "create_edge", {}, iteration=1)
    fake.store[_approval_key("c1", "tc-3")] = "reject"
    decision = await broker.wait("c1", "tc-3", timeout_s=5)
    assert decision == "reject"
    assert _approval_key("c1", "tc-3") not in fake.store


async def test_resolve_flips_pending_key() -> None:
    fake = _FakeValkey()
    await _broker(fake).request("c1", "tc-4", "create_node", {}, iteration=1)
    ok = await resolve_tool_approval("c1", "tc-4", "approve", client=fake)
    assert ok is True
    assert fake.store[_approval_key("c1", "tc-4")] == "approve"


async def test_resolve_misses_when_no_pending_entry() -> None:
    fake = _FakeValkey()
    assert await resolve_tool_approval("c1", "ghost", "approve", client=fake) is False


async def test_resolve_misses_when_already_decided() -> None:
    fake = _FakeValkey()
    fake.store[_approval_key("c1", "tc-5")] = "approve"
    assert await resolve_tool_approval("c1", "tc-5", "reject", client=fake) is False


async def test_resolve_normalizes_decision_values() -> None:
    """Only approve/reject are storable; anything else is rejected as reject."""
    fake = _FakeValkey()
    await _broker(fake).request("c1", "tc-6", "create_node", {}, iteration=1)
    ok = await resolve_tool_approval("c1", "tc-6", "banana", client=fake)  # type: ignore[arg-type]
    assert ok is True
    assert fake.store[_approval_key("c1", "tc-6")] == "reject"


async def test_wait_without_client_fails_closed() -> None:
    """No Valkey available -> deny rather than run the tool."""
    broker = ValkeyApprovalBroker(client_getter=lambda: None)
    decision = await broker.wait("c1", "tc-7", timeout_s=0.2)
    assert decision == "timeout"
