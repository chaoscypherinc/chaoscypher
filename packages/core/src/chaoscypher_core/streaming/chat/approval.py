# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Pending-approval store for chat tool calls.

When the chat LLM emits a mutating tool call under ``tool_approval !=
never-ask``, the streaming handler:

1. Creates a ``PendingApproval`` for the tool_call_id.
2. Emits an SSE ``tool_approval_required`` event so the UI can prompt.
3. Awaits the ``PendingApproval.wait()`` coroutine with a timeout.
4. The UI POSTs to ``/api/v1/chats/{chat_id}/tool_decision`` which calls
   ``resolve()`` on the pending entry, waking the waiter.

Single-user, single-process — an in-memory dict keyed by (chat_id,
tool_call_id) is enough. Cleanup runs automatically after each wait
regardless of approve / reject / timeout.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog


logger = structlog.get_logger(__name__)

Decision = Literal["approve", "reject", "timeout"]


@dataclass
class PendingApproval:
    """A single waiting-for-decision slot.

    Attributes:
        tool_call_id: The LLM-assigned id for the pending tool call.
        tool_name: Human-readable tool name (for UI display).
        arguments: Parsed argument dict the tool would receive.

    """

    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    _event: asyncio.Event = field(default_factory=asyncio.Event)
    _decision: Decision | None = None

    async def wait(self, timeout_seconds: float) -> Decision:
        """Await the user's decision.

        Args:
            timeout_seconds: Maximum wait time before returning ``'timeout'``.

        Returns:
            ``'approve'`` / ``'reject'`` / ``'timeout'`` depending on whether
            ``resolve()`` was called in time. If ``resolve()`` was called
            without a decision value, defaults to ``'reject'`` (fail-closed).

        """
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            return "timeout"
        return self._decision or "reject"

    def resolve(self, decision: Decision) -> None:
        """Set the decision and wake the waiter.

        Args:
            decision: The user's decision.

        """
        self._decision = decision
        self._event.set()


class PendingApprovalStore:
    """In-memory registry of pending tool-call approvals.

    Keyed by ``(chat_id, tool_call_id)``. All access is serialized through
    an ``asyncio.Lock`` so concurrent streaming requests for the same chat
    do not race on create/resolve/cleanup.
    """

    def __init__(self) -> None:
        """Initialize an empty approval store."""
        self._entries: dict[tuple[str, str], PendingApproval] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        chat_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> PendingApproval:
        """Register a new pending approval.

        Args:
            chat_id: Chat the tool call belongs to.
            tool_call_id: LLM-assigned tool call id (must be unique per chat).
            tool_name: Tool name for UI display.
            arguments: Parsed argument dict the tool would receive.

        Returns:
            The newly-registered ``PendingApproval``.

        Raises:
            ValueError: If an entry already exists for ``(chat_id, tool_call_id)``.

        """
        async with self._lock:
            key = (chat_id, tool_call_id)
            if key in self._entries:
                msg = f"Duplicate pending approval: {chat_id=} {tool_call_id=}"
                raise ValueError(msg)
            entry = PendingApproval(
                tool_call_id=tool_call_id, tool_name=tool_name, arguments=arguments
            )
            self._entries[key] = entry
            return entry

    async def resolve(self, chat_id: str, tool_call_id: str, decision: Decision) -> bool:
        """Wake the waiter for this tool call.

        Args:
            chat_id: Chat the tool call belongs to.
            tool_call_id: LLM-assigned tool call id.
            decision: The user's decision.

        Returns:
            ``True`` if a matching pending approval was found and resolved;
            ``False`` if no entry matched (already resolved / never created).

        """
        async with self._lock:
            entry = self._entries.get((chat_id, tool_call_id))
        if entry is None:
            logger.warning(
                "tool_approval_resolve_miss",
                chat_id=chat_id,
                tool_call_id=tool_call_id,
            )
            return False
        entry.resolve(decision)
        return True

    async def cleanup(self, chat_id: str, tool_call_id: str) -> None:
        """Remove an entry. Safe to call repeatedly.

        Args:
            chat_id: Chat the tool call belongs to.
            tool_call_id: LLM-assigned tool call id.

        """
        async with self._lock:
            self._entries.pop((chat_id, tool_call_id), None)


# Module-level singleton — the streaming handler and the REST endpoint
# both import this.
pending_approvals = PendingApprovalStore()


__all__ = [
    "Decision",
    "PendingApproval",
    "PendingApprovalStore",
    "pending_approvals",
]
