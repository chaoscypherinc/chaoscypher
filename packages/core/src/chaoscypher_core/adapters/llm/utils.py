# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LangChain message-conversion utilities.

LangChain-specific helpers used by LLM provider implementations to
translate the project's role/content message format into LangChain
message objects. Framework-agnostic response parsing lives in
``chaoscypher_core.utils.llm_response``.
"""

from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


logger = structlog.get_logger(__name__)


def convert_to_langchain_messages(
    messages: list[dict[str, Any]],
) -> list[SystemMessage | AIMessage | HumanMessage | ToolMessage]:
    """Convert standard message format to LangChain message objects.

    Args:
        messages: List of message dicts with 'role' and 'content'.
            Content can be a string or a list of content blocks for
            multimodal messages (e.g., text + image).
            Example: [{"role": "user", "content": "Hello"}]

    Returns:
        List of LangChain BaseMessage objects

    Example:
        >>> messages = [{"role": "user", "content": "Hello"}]
        >>> lc_messages = convert_to_langchain_messages(messages)
        >>> isinstance(lc_messages[0], HumanMessage)
        True

    """
    lc_messages: list[SystemMessage | AIMessage | HumanMessage | ToolMessage] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
        elif role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "tool":
            # Tool response messages
            lc_messages.append(
                ToolMessage(content=content, tool_call_id=msg.get("tool_call_id", ""))
            )
        else:
            # Default to human message
            lc_messages.append(HumanMessage(content=content))

    return lc_messages


def format_tool_calls_response(tool_calls: list[dict]) -> list[dict]:
    """Format LangChain tool calls to standard format.

    Args:
        tool_calls: LangChain tool_calls list

    Returns:
        Standardized tool calls format

    Example:
        >>> lc_tools = [{"id": "123", "name": "search", "args": {"query": "test"}}]
        >>> formatted = format_tool_calls_response(lc_tools)
        >>> formatted[0]["type"]
        'function'

    """
    return [
        {
            "id": tc.get("id", ""),
            "type": "function",
            "function": {"name": tc.get("name", ""), "arguments": tc.get("args", {})},
        }
        for tc in tool_calls
    ]
