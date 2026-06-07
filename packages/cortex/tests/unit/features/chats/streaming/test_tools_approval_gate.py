# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for the approval gate inside ``_execute_tool_batch``.

These tests exercise the wiring that connects the chat settings to the
``pending_approvals`` store and the SSE events emitted around the wait.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.streaming.chat.approval import pending_approvals
from chaoscypher_core.streaming.chat.tools import _execute_tool_batch


pytestmark = pytest.mark.asyncio


def _event_types(chunks: list[bytes]) -> list[str]:
    """Pull the ``type`` field from each SSE ``data:`` JSON payload."""
    types: list[str] = []
    for chunk in chunks:
        for line in chunk.decode("utf-8").splitlines():
            if not line.startswith("data:"):
                continue
            payload = line.removeprefix("data:").strip()
            if not payload:
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(obj.get("type"), str):
                types.append(obj["type"])
    return types


def _make_tool_call(name: str, call_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Build a tool-call dict in the OpenAI function-calling shape."""
    return {
        "id": call_id,
        "function": {"name": name, "arguments": args},
    }


@pytest.fixture
def always_ask_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``tool_approval='always-ask'`` by patching the converter."""
    from chaoscypher_core.settings import ChatSettings

    class _FakeEngineSettings:
        chat = ChatSettings(tool_approval="always-ask")

    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.tools.build_engine_settings",
        lambda _s: _FakeEngineSettings(),
    )


async def test_approval_rejected_skips_execution(
    always_ask_settings: None,
) -> None:
    """Reject decision yields tool_rejected and does NOT execute the tool."""
    _ = always_ask_settings  # activates the monkeypatch fixture
    tool_executor = MagicMock()
    tool_executor.execute_tool = MagicMock()  # sync fail if called
    chat_service = MagicMock()
    messages: list[dict[str, Any]] = []

    tool_call = _make_tool_call("search_nodes", "call-xyz", {"query": "x"})

    async def _run() -> list[bytes]:
        gen = _execute_tool_batch(
            tool_calls=[tool_call],
            current_content="",
            current_thinking=None,
            chat_id="chat-approval-1",
            chat_service=chat_service,
            tool_executor=tool_executor,
            messages_for_llm=messages,
            executed_tool_signatures={},
            iteration=1,
            tool_defaults=None,
            llm_debug=None,
        )
        return [c async for c in gen]

    async def _resolver() -> None:
        # Wait until the streaming code registers the pending approval,
        # then reject it. The polling avoids a race where resolve() runs
        # before create() and logs a miss.
        for _ in range(50):
            async with pending_approvals._lock:
                if ("chat-approval-1", "call-xyz") in pending_approvals._entries:
                    break
            await asyncio.sleep(0.01)
        await pending_approvals.resolve("chat-approval-1", "call-xyz", "reject")

    chunks, _ = await asyncio.gather(_run(), _resolver())

    event_types = _event_types(chunks)
    assert "tool_approval_required" in event_types
    assert "tool_rejected" in event_types
    assert "tool_start" not in event_types
    assert "tool_result" not in event_types

    # Tool executor must not have been called.
    tool_executor.execute_tool.assert_not_called()

    # The synthesized tool-response message is appended so the LLM sees
    # that the call was denied.
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert tool_msgs, "expected a synthesized denial tool message"
    assert "rejected" in tool_msgs[-1]["content"].lower()
