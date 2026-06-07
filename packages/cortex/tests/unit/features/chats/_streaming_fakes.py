# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fake builders for _handle_tool_calls characterization tests.

These helpers exist so each test stays focused on the behavior it pins
down, instead of repeating MagicMock plumbing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock


def make_tool_call(
    name: str,
    arguments: dict[str, Any] | None = None,
    call_id: str = "call_1",
) -> dict[str, Any]:
    """Build a tool-call dict matching the OpenAI function-calling shape."""
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": arguments or {},
        },
    }


async def _async_iter(chunks: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for c in chunks:
        yield c


def make_chat_stream(
    *,
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    thinking: str | None = None,
) -> Any:
    """Build a fake LLM provider stream.

    Yields raw provider chunks with shape ``{"type": ..., ...}``, which is
    the format that ``_process_iteration_stream`` consumes. Do NOT confuse
    this with the normalized ``{"_internal_type": ...}`` chunks that
    ``_process_iteration_stream`` *produces* -- those are an internal
    representation downstream of this fake.
    """
    chunks: list[dict[str, Any]] = []
    if content:
        chunks.append({"type": "content", "delta": content, "accumulated": content})
    if thinking:
        chunks.append({"type": "thinking_delta", "accumulated": thinking})
    chunks.append(
        {
            "type": "done",
            "content": content,
            "thinking": thinking,
            "tool_calls": tool_calls,
        }
    )
    stream = MagicMock()
    stream.__aiter__ = lambda self: _async_iter(chunks)
    stream.aclose = AsyncMock()
    return stream


def make_chat_provider(responses: list[Any]) -> MagicMock:
    """Build a chat provider whose ``chat()`` returns each fake stream in order.

    ``responses`` is a list of objects from ``make_chat_stream()``.
    """
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=responses)
    return provider


def make_tool_executor(results: dict[str, Any]) -> MagicMock:
    """Build a tool executor whose ``execute_tool(name, args)`` returns results.

    ``results[name]`` is returned for known tools. Unknown tools return an empty dict.
    """
    executor = MagicMock()

    async def _execute(name: str, _args: dict[str, Any]) -> Any:
        return results.get(name, {})

    executor.execute_tool = AsyncMock(side_effect=_execute)
    return executor


def make_chat_service() -> MagicMock:
    """Build a chat service that records ``add_message`` calls."""
    svc = MagicMock()
    svc.add_message = MagicMock()
    return svc


def make_settings() -> MagicMock:
    """Build a settings object whose ``llm.thinking_for_tools`` is False."""
    settings = MagicMock()
    settings.llm.thinking_for_tools = False
    settings.llm.thinking_for_chat = False
    settings.chat_context.enable_response_validation = False
    return settings
