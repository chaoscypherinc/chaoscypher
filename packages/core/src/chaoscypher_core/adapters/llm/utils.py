# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LangChain message-conversion utilities.

LangChain-specific helpers used by LLM provider implementations to
translate the project's role/content message format into LangChain
message objects. Framework-agnostic response parsing lives in
``chaoscypher_core.utils.llm_response``.
"""

import json
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


logger = structlog.get_logger(__name__)


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    """Normalize a tool call's ``function.arguments`` into a dict.

    ``format_tool_calls_response`` stores arguments as a dict, but the
    OpenAI wire format (and any history round-tripped through it) carries
    a JSON string. Unparseable input degrades to an empty dict.
    """
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str) and arguments:
        try:
            parsed = json.loads(arguments)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


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
            # Preserve tool_calls: cloud providers reject a ToolMessage whose
            # preceding assistant turn doesn't carry the matching tool_calls.
            tool_calls = msg.get("tool_calls") or []
            lc_tool_calls = [
                {
                    "id": tc.get("id", ""),
                    "name": tc.get("function", {}).get("name", ""),
                    "args": _parse_tool_arguments(tc.get("function", {}).get("arguments")),
                }
                for tc in tool_calls
            ]
            lc_messages.append(AIMessage(content=content, tool_calls=lc_tool_calls))
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
