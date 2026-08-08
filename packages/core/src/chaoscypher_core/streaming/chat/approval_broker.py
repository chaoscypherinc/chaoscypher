# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Valkey-backed tool-approval broker.

Carries tool-approval decisions across processes: the shared chat tool loop
(running in the neuron worker) registers a pending key and polls it; the
``POST /chats/{id}/tool_decision`` endpoint (cortex process) flips the key
to the user's decision. Fail-closed everywhere: a missing Valkey client,
an unanswered request, or an unknown decision value all resolve to denial.

Replaces the single-process in-memory ``pending_approvals`` store, which
only the consumer-less ``/stream`` path could reach (2026-06-10 audit P1:
``tool_approval`` was silently unenforced for web chat).
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any

from chaoscypher_core.utils.logging.app_config import get_logger


logger = get_logger(__name__)

PENDING_SENTINEL = "pending"

# Server-side Lua for the pending->decision flip (Valkey EVAL — not Python
# eval). Mirrors ``QueueClient._STRING_CAS_SCRIPT``; duplicated because
# ``resolve_tool_approval`` receives a raw Valkey client, not the queue
# wrapper. Keep the two in sync.
_RESOLVE_CAS_SCRIPT = (
    "local v = redis.call('GET', KEYS[1]) "
    "if v == ARGV[1] then redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL') return 1 end "
    "return 0"
)

# Grace added to the key TTL beyond the decision timeout so a decision
# arriving at the buzzer still lands on a live key.
_TTL_GRACE_SECONDS = 60


def _approval_key(chat_id: str, tool_call_id: str) -> str:
    """Valkey key carrying one tool call's approval state."""
    return f"chat:approval:{chat_id}:{tool_call_id}"


def _default_client() -> Any:
    """Return the process-wide Valkey client (lazy import avoids cycles)."""
    from chaoscypher_core.queue.client import queue_client

    return queue_client.client


class ValkeyApprovalBroker:
    """ApprovalBroker implementation over short-lived Valkey keys.

    The Valkey client is resolved lazily through ``client_getter`` so the
    broker can be constructed at wiring time (before the queue connects)
    and so tests can inject a fake.
    """

    def __init__(self, client_getter: Callable[[], Any] | None = None) -> None:
        """Bind the broker to a client source.

        Args:
            client_getter: Returns the async Valkey client (or None when
                unavailable). Defaults to the shared queue client.

        """
        self._client_getter = client_getter or _default_client

    async def request(
        self,
        chat_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        iteration: int,
    ) -> None:
        """Register a pending-decision key for this tool call.

        Args:
            chat_id: Chat the tool call belongs to.
            tool_call_id: LLM-assigned tool call id.
            tool_name: Tool name (logged for traceability).
            arguments: Parsed argument dict (logged for traceability).
            iteration: Tool-loop iteration number.

        """
        client = self._client_getter()
        if client is None:
            logger.warning(
                "tool_approval_request_skipped",
                chat_id=chat_id,
                tool_call_id=tool_call_id,
                reason="valkey_unavailable",
            )
            return

        from chaoscypher_core.app_config import get_settings

        ttl = get_settings().chat.tool_approval_timeout_seconds + _TTL_GRACE_SECONDS
        await client.set(_approval_key(chat_id, tool_call_id), PENDING_SENTINEL, ex=ttl)
        logger.info(
            "tool_approval_requested",
            chat_id=chat_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            iteration=iteration,
        )

    async def wait(self, chat_id: str, tool_call_id: str, timeout_s: float) -> str:
        """Poll for the user's decision.

        Args:
            chat_id: Chat the tool call belongs to.
            tool_call_id: LLM-assigned tool call id.
            timeout_s: Maximum wait before returning ``'timeout'``.

        Returns:
            ``'approve'`` / ``'reject'`` / ``'timeout'`` (fail-closed).

        """
        from chaoscypher_core.app_config import get_settings

        poll_s = get_settings().intervals.chat_approval_poll_ms / 1000
        key = _approval_key(chat_id, tool_call_id)
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            client = self._client_getter()
            if client is not None:
                value = await client.get(key)
                if isinstance(value, bytes):
                    value = value.decode()
                if value in ("approve", "reject"):
                    await client.delete(key)
                    return value
            await asyncio.sleep(min(poll_s, max(deadline - time.monotonic(), 0)))

        logger.info(
            "tool_approval_timed_out",
            chat_id=chat_id,
            tool_call_id=tool_call_id,
            timeout_s=timeout_s,
        )
        return "timeout"


async def resolve_tool_approval(
    chat_id: str,
    tool_call_id: str,
    decision: str,
    client: Any = None,
) -> bool:
    """Flip a pending approval key to the user's decision.

    Called by the REST endpoint. Only an existing PENDING key can be
    resolved — unknown or already-decided calls return False so the
    endpoint can 404.

    Args:
        chat_id: Chat the tool call belongs to.
        tool_call_id: LLM-assigned tool call id.
        decision: ``'approve'`` or ``'reject'``; anything else is treated
            as ``'reject'`` (fail-closed).
        client: Async Valkey client override (tests); defaults to the
            shared queue client.

    Returns:
        True when a pending entry was found and resolved.

    """
    client = client if client is not None else _default_client()
    if client is None:
        logger.warning(
            "tool_approval_resolve_skipped",
            chat_id=chat_id,
            tool_call_id=tool_call_id,
            reason="valkey_unavailable",
        )
        return False

    key = _approval_key(chat_id, tool_call_id)
    normalized = decision if decision in ("approve", "reject") else "reject"

    # One atomic compare-and-swap: the old read-then-write let two
    # concurrent resolvers carrying conflicting decisions both observe
    # PENDING and both write — last write silently overrode the first
    # decision (2026-06-28 finding; first-decision-wins decided
    # 2026-07-23). Server-side Lua flips the key only while it still
    # holds the pending sentinel, keeping the TTL. This is the same
    # script as ``QueueClient.compare_and_swap_string`` — duplicated
    # because this endpoint receives a raw Valkey client, not the queue
    # wrapper; keep the two in sync.
    resolved = await client.eval(_RESOLVE_CAS_SCRIPT, 1, key, PENDING_SENTINEL, normalized)
    if not int(resolved or 0):
        return False

    logger.info(
        "tool_approval_resolved",
        chat_id=chat_id,
        tool_call_id=tool_call_id,
        decision=normalized,
    )
    return True


__all__ = [
    "PENDING_SENTINEL",
    "ValkeyApprovalBroker",
    "resolve_tool_approval",
]
