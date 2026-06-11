# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool-call dedup, guidance, and retry helpers for the shared chat loop.

Duplicate-call signature tracking, corrective guidance generation, the
intent-fragment / leaked-tool-call detectors, and the unfulfilled-intent
retry. The loop itself lives in :mod:`chaoscypher_core.streaming.chat.loop`.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.app_config import get_settings as _get_settings
from chaoscypher_core.services.chat.engine.constants import (
    MAX_TOOL_ITERATIONS,
    MAX_TOTAL_TOOL_CALLS,
)


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings

# Superset of phrases indicating the LLM intends to take action.
# Used by _force_final_answer_if_intent and _retry_unfulfilled_intent.
_INTENT_PHRASES = (
    "let me",
    "i'll",
    "i will",
    "now i",
    "i'll check",
    "i'll search",
    "i'll look",
    "i'll find",
    "i'll get",
    "let me check",
    "let me search",
    "let me look",
    "let me find",
    "let me get",
    "now i'll",
    "now let me",
    "i will check",
    "i will search",
    "i will get",
    "i need to check",
    "i need to get",
    "i need to search",
    "i need to find",
)

# Content shorter than this that contains an intent phrase is treated as an
# unfulfilled-intent narration fragment rather than a substantive answer.
_SUBSTANTIVE_CONTENT_MIN_CHARS = 200


def is_intent_fragment(content: str | None) -> bool:
    """True when content is a short "let me / I'll ..." narration, not an answer.

    Local models routinely narrate the next tool call ("Now let me get the
    connections...") instead of answering; when a tool loop ends on such a
    fragment, the caller should force one final no-tools answer rather than
    present the narration as the result. Empty content returns False — the
    empty case has its own recovery path.

    Args:
        content: Candidate final response content.

    Returns:
        True when the content is short and contains an intent phrase.

    """
    text = content or ""
    if not text.strip() or len(text) > _SUBSTANTIVE_CONTENT_MIN_CHARS:
        return False
    lowered = text.lower()
    return any(phrase in lowered for phrase in _INTENT_PHRASES)


# Markers of a tool call leaked into text content. Local models sometimes emit
# tool-call syntax — their own template's or a hallucinated foreign one — as
# plain text instead of a native structured call (live failure cb4c1618:
# qwen3:30b produced Anthropic-style '<invoke name="search_nodes">' XML).
# Content carrying these markers is never an answer, whatever its length.
_TOOL_CALL_LEAK_MARKERS = (
    "<tool_call",  # qwen / hermes template: <tool_call> / <tool_calls>
    "</tool_call",
    "<invoke ",  # hallucinated Anthropic-style XML
    "<function_call",
    "[tool_calls]",  # mistral template (content is compared lowercase)
    "<|tool",  # chatml-style tool sentinels
)


def contains_leaked_tool_call(content: str | None) -> bool:
    """True when content contains tool-call syntax leaked as plain text.

    Args:
        content: Candidate final response content.

    Returns:
        True when any known tool-call marker appears in the content.

    """
    if not content:
        return False
    lowered = content.lower()
    return any(marker in lowered for marker in _TOOL_CALL_LEAK_MARKERS)


logger = structlog.get_logger(__name__)


async def _close_stream(stream: Any) -> None:
    """Close an async stream if it supports aclose.

    Args:
        stream: Stream object to close

    """
    if hasattr(stream, "aclose"):
        await stream.aclose()


def _extract_tool_defaults(available_tools: list[Any]) -> dict[str, dict[str, Any]]:
    """Build a map of tool name -> {param: default_value} from tool schemas.

    Used to normalize tool call arguments before signature computation so
    that calls with/without explicit default values produce the same signature.

    Args:
        available_tools: Tool schemas in OpenAI function-calling format.

    Returns:
        Dict mapping tool name to a dict of parameter defaults.

    """
    tool_defaults: dict[str, dict[str, Any]] = {}
    for tool in available_tools:
        func = tool.get("function", {})
        name = func.get("name", "")
        if not name:
            continue
        props = func.get("parameters", {}).get("properties", {})
        defaults: dict[str, Any] = {}
        for param_name, param_schema in props.items():
            if "default" in param_schema:
                defaults[param_name] = param_schema["default"]
        if defaults:
            tool_defaults[name] = defaults
    return tool_defaults


def _normalize_tool_args(
    arguments: dict[str, Any],
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Strip null, empty, and schema-default values from tool arguments.

    LLMs sometimes include optional parameters with default/empty values in
    some iterations and omit them in others.  Stripping these before signature
    computation ensures those variations are treated as identical calls.

    Args:
        arguments: Raw arguments dict from the tool call.
        defaults: Schema default values for this tool's parameters.

    Returns:
        Cleaned dict with only meaningful non-default values, strings trimmed.

    """
    result: dict[str, Any] = {}
    for key, val in arguments.items():
        if val is None:
            continue
        if val == "":
            continue
        if isinstance(val, list) and len(val) == 0:
            continue
        # Strip values that match the tool's schema default
        if defaults and key in defaults and val == defaults[key]:
            continue
        result[key] = val.strip() if isinstance(val, str) else val
    return result


def _tool_call_signature(
    tool_call: dict[str, Any],
    tool_defaults: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Compute a normalized signature for a tool call.

    Always parses arguments to a dict first, strips empty/null optional
    params and schema-default values, then serializes with
    ``json.dumps(sort_keys=True)`` so that string-vs-dict format differences
    and optional-param variations don't break deduplication.

    Args:
        tool_call: Tool call dict with function.name and function.arguments.
        tool_defaults: Map of tool name -> {param: default_value} from schemas.

    Returns:
        Tuple of (tool_name, signature_string).

    """
    func = tool_call.get("function", {})
    tool_name = func.get("name", "")
    arguments = func.get("arguments", {})
    # Normalize: always parse to dict then serialize consistently
    if isinstance(arguments, str):
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            arguments = json.loads(arguments)
    if isinstance(arguments, dict):
        defaults = tool_defaults.get(tool_name) if tool_defaults else None
        arguments = _normalize_tool_args(arguments, defaults)
    args_str = (
        json.dumps(arguments, sort_keys=True) if isinstance(arguments, dict) else str(arguments)
    )
    return tool_name, f"{tool_name}:{args_str}"


def _filter_duplicate_tool_calls(
    tool_calls: list[Any],
    executed_signatures: dict[str, int],
    chat_id: str,
    iteration: int,
    tool_defaults: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[Any], list[Any]]:
    """Filter out duplicate tool calls that have already been executed.

    Args:
        tool_calls: Tool calls to filter.
        executed_signatures: Map of "tool_name:args_json" -> call count.
        chat_id: Chat ID for logging.
        iteration: Current iteration number.
        tool_defaults: Schema defaults for signature normalization.

    Returns:
        Tuple of (filtered_calls, duplicate_calls) where duplicate_calls
        preserves the full tool call objects for frontend display.

    """
    filtered: list[Any] = []
    duplicates: list[Any] = []
    seen_in_batch: set[str] = set()
    for tc in tool_calls:
        tool_name, signature = _tool_call_signature(tc, tool_defaults)

        call_count = executed_signatures.get(signature, 0)
        is_duplicate = call_count >= 1 or signature in seen_in_batch
        logger.debug(
            "chat_stream_tool_call_signature",
            chat_id=chat_id,
            tool_name=tool_name,
            iteration=iteration,
            signature=signature,
            is_duplicate=is_duplicate,
        )
        if is_duplicate:
            duplicates.append(tc)
            logger.warning(
                "chat_stream_duplicate_tool_call_skipped",
                chat_id=chat_id,
                tool_name=tool_name,
                iteration=iteration,
                previous_count=call_count,
                within_batch=signature in seen_in_batch,
            )
        else:
            seen_in_batch.add(signature)
            filtered.append(tc)
    return filtered, duplicates


def _track_tool_signature(
    tool_call: dict[str, Any],
    executed_signatures: dict[str, int],
    tool_defaults: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Record a tool call signature in the execution tracker.

    Args:
        tool_call: Tool call dict with function.name and function.arguments.
        executed_signatures: Mutable map of "tool_name:args_json" -> call count.
        tool_defaults: Schema defaults for signature normalization.

    """
    _name, signature = _tool_call_signature(tool_call, tool_defaults)
    executed_signatures[signature] = executed_signatures.get(signature, 0) + 1


def _extract_found_nodes(messages_for_llm: list) -> list[dict]:
    """Extract node IDs and names from previous tool results.

    Args:
        messages_for_llm: Current message history including tool results

    Returns:
        List of dicts with 'id' and 'name' keys

    """
    found_nodes: list[dict] = []

    for msg in messages_for_llm:
        if msg.get("role") == "tool" and msg.get("name") in ("search_nodes", "resolve_node"):
            try:
                result = json.loads(msg.get("content", "{}"))
                # Handle search_nodes results (list of nodes)
                if isinstance(result, dict) and "results" in result:
                    found_nodes.extend(
                        {
                            "id": node["id"],
                            "name": node.get("name", node.get("label", "Unknown")),
                        }
                        for node in result.get("results", [])
                        if isinstance(node, dict) and "id" in node
                    )
                # Handle resolve_node result (single node)
                elif isinstance(result, dict) and "id" in result:
                    found_nodes.append(
                        {
                            "id": result["id"],
                            "name": result.get("name", result.get("label", "Unknown")),
                        }
                    )
            except json.JSONDecodeError, TypeError:
                pass

    return found_nodes


def _generate_unfulfilled_guidance(messages_for_llm: list, llm_content: str) -> str:
    """Generate guidance when LLM describes action without calling tool.

    Args:
        messages_for_llm: Current message history
        llm_content: What the LLM said it would do

    Returns:
        Specific guidance with node IDs if available

    """
    found_nodes = _extract_found_nodes(messages_for_llm)
    content_lower = (llm_content or "").lower()

    # Check if LLM mentioned edges/relationships
    edge_keywords = ["edge", "relationship", "connection", "connected", "related"]
    wants_edges = any(kw in content_lower for kw in edge_keywords)

    if found_nodes and wants_edges:
        # LLM wants to check relationships but didn't call the tool
        node_examples = found_nodes[:2]
        guidance = (
            "You said you would check relationships but didn't call any tool. "
            "Call get_node_edges NOW with one of these node IDs:\n"
        )
        for node in node_examples:
            guidance += f'- get_node_edges(node_id="{node["id"]}") for {node["name"]}\n'
        if len(found_nodes) >= 2:
            guidance += (
                f'\nOr use traverse_path(source_node_id="{found_nodes[0]["id"]}", '
                f'target_node_id="{found_nodes[1]["id"]}") to find paths between them.'
            )
        return guidance

    if found_nodes:
        # Generic - we have nodes, tell LLM to use them
        node_list = ", ".join([f"{n['name']} (id: {n['id']})" for n in found_nodes[:3]])
        return (
            f"You have already found these nodes: {node_list}. "
            "Don't describe what you'll do - actually CALL the tool. "
            'Use get_node_edges(node_id="...") to find relationships, '
            "or traverse_path to find connections between nodes."
        )

    # No nodes found yet - generic guidance
    return (
        "You said you would check/search for something but didn't call any tools. "
        "Please actually use the appropriate tool (like search_nodes, get_node_edges, "
        "or search_chunks) to complete your task. Don't just describe what you'll do - "
        "call the tool now."
    )


def _generate_duplicate_guidance(messages_for_llm: list, dup_names: list[str]) -> str:
    """Generate context-aware guidance when duplicate tool calls are detected.

    Examines the message history to find node IDs from previous search results
    and provides specific guidance about what tools to use next.

    Args:
        messages_for_llm: Current message history including tool results
        dup_names: Names of the duplicate tools that were called

    Returns:
        Guidance message for the LLM

    """
    found_nodes = _extract_found_nodes(messages_for_llm)

    # Build specific guidance based on what we found
    if found_nodes and "search_nodes" in dup_names:
        # We have node IDs but LLM keeps searching - tell it to get edges
        node_list = ", ".join([f"{n['name']} (id: {n['id']})" for n in found_nodes[:3]])
        guidance = (
            f"STOP repeating search_nodes. You already found these nodes: {node_list}. "
            "Now use get_node_edges to find their relationships. Call it like this:\n"
        )
        for node in found_nodes[:2]:
            guidance += f'- get_node_edges(node_id="{node["id"]}") for {node["name"]}\n'
        if len(found_nodes) >= 2:
            guidance += (
                f'\nOr use traverse_path(source_node_id="{found_nodes[0]["id"]}", '
                f'target_node_id="{found_nodes[1]["id"]}") to find paths between them.\n'
            )
        guidance += "Do NOT call search_nodes again. Use the node IDs you already have."
        return guidance

    # Generic fallback guidance
    return (
        f"You already called {', '.join(dup_names)} with the same arguments. "
        "The exact data may not exist in the graph. Either:\n"
        "1. Use search_chunks to find text passages with the information\n"
        "2. Provide your answer based on what you've already found\n"
        "Do NOT repeat the same tool calls."
    )


async def _process_iteration_stream(
    stream_result: Any,
    chat_id: str,
    iteration: int,
) -> AsyncIterator[dict[str, Any]]:
    """Process an LLM stream within an iteration and detect tool calls.

    This is an internal helper that yields dicts with _internal_type field
    for the caller to process. It does NOT finalize or save messages.

    Args:
        stream_result: Streaming result from LLM call
        chat_id: Chat ID for logging
        iteration: Current iteration number

    Yields:
        Internal chunks with _internal_type: "content", "thinking", or "done"
        The "done" chunk may include tool_calls if the LLM requested more tools

    """
    if not hasattr(stream_result, "__aiter__"):
        yield {"_internal_type": "done", "content": "", "thinking": None, "tool_calls": None}
        return

    accumulated_content = ""
    thinking = None

    async for chunk in stream_result:
        chunk_type = chunk.get("type")

        if chunk_type == "content":
            delta = chunk.get("delta", "")
            accumulated_content = chunk.get("accumulated", accumulated_content)
            yield {
                "_internal_type": "content",
                "delta": delta,
                "accumulated": accumulated_content,
            }

        elif chunk_type == "thinking_delta":
            thinking = chunk.get("accumulated", "")
            yield {"_internal_type": "thinking", "thinking": thinking}

        elif chunk_type == "error":
            error_msg = chunk.get("error", "Unknown LLM error")
            logger.error(
                "chat_stream_iteration_provider_error",
                chat_id=chat_id,
                iteration=iteration,
                error=error_msg,
            )
            yield {
                "_internal_type": "error",
                "error": error_msg,
                "error_code": chunk.get("error_code", "LLM_ERROR"),
            }
            return

        elif chunk_type == "done":
            accumulated_content = chunk.get("content", accumulated_content)
            thinking = chunk.get("thinking", thinking)
            tool_calls = chunk.get("tool_calls")

            logger.info(
                "chat_stream_iteration_done",
                chat_id=chat_id,
                iteration=iteration,
                content_length=len(accumulated_content) if accumulated_content else 0,
                has_tool_calls=bool(tool_calls),
                tool_call_count=len(tool_calls) if tool_calls else 0,
            )

            yield {
                "_internal_type": "done",
                "content": accumulated_content,
                "thinking": thinking,
                "tool_calls": tool_calls,
                "provider_timings": chunk.get("provider_timings"),
                "usage": chunk.get("usage"),
                "finish_reason": chunk.get("finish_reason"),
            }
            return

    # Stream ended without done chunk - return what we have
    yield {
        "_internal_type": "done",
        "content": accumulated_content,
        "thinking": thinking,
        "tool_calls": None,
    }


async def _retry_unfulfilled_intent(
    followup_content: str,
    iteration: int,
    messages_for_llm: list[Any],
    chat_provider: Any,
    available_tools: list[Any],
    chat_id: str,
    settings: Settings,
) -> list[Any] | None:
    """Retry when LLM describes actions without emitting tool calls.

    Detects phrases like "I'll check", "let me search" in the LLM response,
    and if found, prompts the LLM to actually call the tools.

    Args:
        followup_content: Content from the follow-up LLM call
        iteration: Current iteration number
        messages_for_llm: Current message history (mutated if retry attempted)
        chat_provider: LLM provider instance
        available_tools: Available tools list
        chat_id: Chat ID for logging
        settings: Application settings for thinking configuration

    Returns:
        List of tool calls if retry succeeded, None otherwise

    """
    content_lower = (followup_content or "").lower()
    # A tool call leaked as text ("<tool_calls><invoke ...>") is unfulfilled
    # intent in its strongest form: the model TRIED to call a tool but spoke
    # the wrong dialect. Always retry those, whatever the content length.
    leaked_tool_call = contains_leaked_tool_call(followup_content)
    has_unfulfilled_intent = leaked_tool_call or any(
        phrase in content_lower for phrase in _INTENT_PHRASES
    )

    # Live settings, falling back to the schema default (mirrors the loop's
    # _tool_limits fail-open behavior for mocked settings).
    max_iters = getattr(getattr(settings, "chat", None), "max_tool_iterations", None)
    if not (isinstance(max_iters, int) and max_iters > 0):
        max_iters = MAX_TOOL_ITERATIONS
    if not has_unfulfilled_intent or iteration >= max_iters - 1:
        return None

    # Skip retry when the LLM already provided a substantive answer.
    # Short responses like "Let me search for X" are genuine unfulfilled
    # intent, but longer responses with citations or detailed content are
    # real answers that happen to use conversational transition phrases.
    # (Never applies to leaked tool calls — those are not answers at any length.)
    content_len = len(followup_content or "")
    if not leaked_tool_call and content_len > _SUBSTANTIVE_CONTENT_MIN_CHARS:
        logger.info(
            "chat_stream_unfulfilled_intent_skipped_substantial",
            chat_id=chat_id,
            iteration=iteration,
            content_length=content_len,
        )
        return None

    logger.warning(
        "chat_stream_unfulfilled_intent_detected",
        chat_id=chat_id,
        iteration=iteration,
        content_preview=followup_content[: _get_settings().chat_context.content_preview_length]
        if followup_content
        else "",
    )

    # Generate specific guidance based on context
    unfulfilled_guidance = _generate_unfulfilled_guidance(messages_for_llm, followup_content)
    messages_for_llm.append({"role": "assistant", "content": followup_content})
    messages_for_llm.append({"role": "user", "content": unfulfilled_guidance})

    # Make another LLM call to get the actual tool call
    retry_result = await chat_provider.chat(
        messages=messages_for_llm,
        tools=available_tools,
        stream=True,
        enable_thinking=settings.llm.thinking_for_tools,
    )
    retry_tool_calls = None
    try:
        async for chunk in _process_iteration_stream(
            stream_result=retry_result,
            chat_id=chat_id,
            iteration=iteration,
        ):
            chunk_type = chunk.get("_internal_type")
            if chunk_type == "done":
                retry_tool_calls = chunk.get("tool_calls")
                if retry_tool_calls:
                    logger.info(
                        "chat_stream_retry_got_tools",
                        chat_id=chat_id,
                        tool_count=len(retry_tool_calls),
                    )
                break
    finally:
        await _close_stream(retry_result)

    return retry_tool_calls


__all__ = [
    "MAX_TOOL_ITERATIONS",
    "MAX_TOTAL_TOOL_CALLS",
    "_close_stream",
    "_extract_tool_defaults",
    "_filter_duplicate_tool_calls",
    "_generate_duplicate_guidance",
    "_process_iteration_stream",
    "_retry_unfulfilled_intent",
    "_track_tool_signature",
    "contains_leaked_tool_call",
    "is_intent_fragment",
]
