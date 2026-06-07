# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Call Handling and Execution.

Manages tool call deduplication, execution, signature tracking,
duplicate guidance generation, and follow-up LLM calls within
the streaming chat tool-calling loop.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.app_config import get_settings as _get_settings
from chaoscypher_core.app_config.engine_factory import (
    build_engine_settings,
)
from chaoscypher_core.services.chat.engine.constants import (
    MAX_TOOL_ITERATIONS,
    MAX_TOTAL_TOOL_CALLS,
)
from chaoscypher_core.streaming.chat.approval import pending_approvals
from chaoscypher_core.streaming.chat.utils import (
    format_sse_event,
    parse_tool_arguments,
    strip_thinking_tags,
)


# Seconds to wait for the user to approve/reject a mutating tool call.
# Kept modest — if the user steps away, the stream should bail rather than
# tie up an LLM slot indefinitely.
_APPROVAL_TIMEOUT_SECONDS: float = 300.0


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


logger = structlog.get_logger(__name__)


async def _close_stream(stream: Any) -> None:
    """Close an async stream if it supports aclose.

    Args:
        stream: Stream object to close

    """
    if hasattr(stream, "aclose"):
        await stream.aclose()


@dataclass
class ToolCallingState:
    """Mutable state for the ``_handle_tool_calls`` iteration loop.

    Groups the variables that previously lived as 10 bare locals so the
    loop body has a single state object to mutate. Field names match the
    original local names so behavior is preserved.

    Lifetimes:
        - ``iteration``, ``total_tool_calls``: counters that grow until
          the loop terminates.
        - ``current_*``: refreshed every iteration with the latest
          tool_calls / content / thinking from the follow-up call.
        - ``latest_content``, ``all_thinking_parts``,
          ``executed_tool_signatures``, ``all_cached_tool_calls``:
          accumulated across iterations and used by ``_finalize_tool_response``.
    """

    current_tool_calls: list[Any] | None
    current_content: str
    current_thinking: str | None
    latest_content: str
    iteration: int = 0
    total_tool_calls: int = 0
    all_thinking_parts: list[str] = field(default_factory=list)
    executed_tool_signatures: dict[str, int] = field(default_factory=dict)
    error_occurred: bool = False
    all_cached_tool_calls: list[Any] = field(default_factory=list)


def _check_tool_call_limit(
    total_tool_calls: int,
    chat_id: str,
    iteration: int,
) -> bytes | None:
    """Check if total tool call limit has been exceeded.

    Args:
        total_tool_calls: Total tool calls made so far
        chat_id: Chat ID for logging
        iteration: Current iteration number

    Returns:
        SSE warning event if limit exceeded, None otherwise

    """
    if total_tool_calls <= MAX_TOTAL_TOOL_CALLS:
        return None

    logger.warning(
        "chat_stream_tool_limit_exceeded",
        chat_id=chat_id,
        total_tool_calls=total_tool_calls,
        max_allowed=MAX_TOTAL_TOOL_CALLS,
    )
    return format_sse_event(
        "warning",
        {
            "message": f"Tool call limit reached ({MAX_TOTAL_TOOL_CALLS}). "
            "Finalizing response with current results.",
            "iteration": iteration,
        },
    )


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


async def _execute_tool_batch(
    tool_calls: list[Any],
    current_content: str,
    current_thinking: str | None,
    chat_id: str,
    chat_service: Any,
    tool_executor: Any,
    messages_for_llm: list[Any],
    executed_tool_signatures: dict[str, int],
    iteration: int,
    tool_defaults: dict[str, dict[str, Any]] | None = None,
    llm_debug: Any | None = None,
) -> AsyncIterator[bytes]:
    """Execute a batch of tool calls with message tracking.

    Notifies the client about tool calls, saves the assistant message,
    executes each tool, and tracks signatures for duplicate detection.

    Args:
        tool_calls: Tool calls to execute.
        current_content: Current assistant content.
        current_thinking: Current thinking content.
        chat_id: Chat ID for logging.
        chat_service: ChatService instance.
        tool_executor: ToolExecutorService instance.
        messages_for_llm: Current message history (mutated).
        executed_tool_signatures: Signature tracker (mutated).
        iteration: Current iteration number.
        tool_defaults: Schema defaults for signature normalization.
        llm_debug: Optional debug info for timing data.

    Yields:
        SSE-formatted response chunks.

    """
    # Notify about tool calls for this iteration
    yield format_sse_event("tool_calls", {"tool_calls": tool_calls, "iteration": iteration})

    # Add assistant message with tool calls to LLM context (not saved to DB).
    # Only _save_and_emit_done() persists the final assistant message to avoid
    # duplicate bubbles when the chat is reloaded from the database.
    clean_content = strip_thinking_tags(current_content)
    messages_for_llm.append(
        {
            "role": "assistant",
            "content": clean_content,
            "tool_calls": tool_calls,
        }
    )

    # Resolve the approval policy once per batch. The settings are read
    # fresh each iteration so a live settings-yaml change takes effect on
    # the next tool batch without reloading the whole worker.
    engine_settings = build_engine_settings(_get_settings())
    approval_mode = engine_settings.chat.tool_approval
    mutating_tools = set(engine_settings.chat.mutating_tools)

    def _needs_approval(tool_name: str) -> bool:
        """Return True if this tool requires user confirmation before running."""
        if approval_mode == "never-ask":
            return False
        if approval_mode == "always-ask":
            return True
        # "ask-on-write" — only mutating tools.
        return tool_name in mutating_tools

    # Execute each tool call, gating mutating calls behind user approval
    # when the configured policy demands it.
    for tool_call in tool_calls:
        tool_name = tool_call.get("function", {}).get("name", "")
        tool_call_id = tool_call.get("id") or tool_call.get("tool_call_id", "")

        # Parse arguments for display in the approval dialog. We intentionally
        # swallow parse errors here — the approval UI just needs something
        # human-readable, and the real parse happens later in _execute_single_tool.
        raw_args = tool_call.get("function", {}).get("arguments", "{}")
        try:
            args_for_display = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (ValueError, TypeError):  # fmt: skip
            args_for_display = {}
        if not isinstance(args_for_display, dict):
            args_for_display = {"_raw": args_for_display}

        if tool_call_id and _needs_approval(tool_name):
            entry = await pending_approvals.create(
                chat_id=chat_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=args_for_display,
            )
            yield format_sse_event(
                "tool_approval_required",
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": args_for_display,
                    "iteration": iteration,
                },
            )
            try:
                decision = await entry.wait(timeout_seconds=_APPROVAL_TIMEOUT_SECONDS)
            finally:
                await pending_approvals.cleanup(chat_id, tool_call_id)

            if decision != "approve":
                # Synthesize a tool-response message and skip execution so the
                # LLM knows the call was denied and can continue the conversation.
                denied_content = f"User {decision}ed this tool call. Do not retry."
                messages_for_llm.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": denied_content,
                    }
                )
                yield format_sse_event(
                    "tool_rejected",
                    {
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "decision": decision,
                    },
                )
                # Record the signature so the duplicate-detection logic
                # still counts this tool call as "attempted" and the LLM
                # cannot loop on the same denied call forever.
                _track_tool_signature(tool_call, executed_tool_signatures, tool_defaults)
                continue

        _track_tool_signature(tool_call, executed_tool_signatures, tool_defaults)
        tool_chunk: bytes
        async for tool_chunk in _execute_single_tool(
            tool_call=tool_call,
            chat_id=chat_id,
            chat_service=chat_service,
            tool_executor=tool_executor,
            messages_for_llm=messages_for_llm,
            iteration=iteration,
            llm_debug=llm_debug,
        ):
            yield tool_chunk


async def _execute_single_tool(
    tool_call: dict[str, Any],
    chat_id: str,
    chat_service: Any,
    tool_executor: Any,
    messages_for_llm: list[Any],
    iteration: int = 1,
    llm_debug: Any | None = None,
) -> AsyncIterator[bytes]:
    """Execute a single tool call.

    Args:
        tool_call: Tool call to execute
        chat_id: Chat ID for logging
        chat_service: ChatService instance
        tool_executor: ToolExecutorService instance
        messages_for_llm: Current message history
        iteration: Current iteration number (for logging/events)
        llm_debug: Optional debug info for timing data

    Yields:
        SSE-formatted response chunks

    """
    function = tool_call.get("function", {})
    tool_name = function.get("name")
    arguments_raw = function.get("arguments", {})
    arguments = parse_tool_arguments(arguments_raw, tool_name, chat_id)

    logger.info(
        "chat_stream_tool_executing",
        chat_id=chat_id,
        tool_name=tool_name,
        iteration=iteration,
    )
    yield format_sse_event(
        "tool_start", {"tool": tool_name, "arguments": arguments, "iteration": iteration}
    )

    # Execute tool with timing
    tool_start = time.monotonic()
    result = await tool_executor.execute_tool(tool_name, arguments)
    tool_duration_ms = round((time.monotonic() - tool_start) * 1000)
    result_preview = json.dumps(result)[: _get_settings().chat_context.json_result_preview_length]
    logger.info(
        "chat_stream_tool_result",
        chat_id=chat_id,
        tool_name=tool_name,
        result_preview=result_preview,
        iteration=iteration,
        duration_ms=tool_duration_ms,
    )

    # Store per-tool timing on llm_debug
    if llm_debug is not None:
        tool_entry = {
            "name": tool_name,
            "args_preview": json.dumps(arguments)[:80],
            "duration_ms": tool_duration_ms,
            "iteration": iteration,
            "tool_call_id": tool_call.get("id"),
        }
        llm_debug.timing.setdefault("tool_calls", []).append(tool_entry)

    yield format_sse_event(
        "tool_result",
        {
            "tool": tool_name,
            "result": result,
            "iteration": iteration,
            "tool_call_id": tool_call.get("id"),
            "duration_ms": tool_duration_ms,
        },
    )

    # Add tool result to chat and messages
    chat_service.add_message(
        chat_id,
        "tool",
        json.dumps(result),
        {"tool_call_id": tool_call.get("id"), "name": tool_name},
    )
    messages_for_llm.append(
        {
            "role": "tool",
            "content": json.dumps(result),
            "tool_call_id": tool_call.get("id"),
            "name": tool_name,
        }
    )


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
            }
            return

    # Stream ended without done chunk - return what we have
    yield {
        "_internal_type": "done",
        "content": accumulated_content,
        "thinking": thinking,
        "tool_calls": None,
    }


async def _retry_all_duplicates_path(
    duplicate_calls: list[Any],
    messages_for_llm: list[Any],
    chat_provider: Any,
    available_tools: list[Any],
    chat_id: str,
    iteration: int,
    latest_content: str,
    state: dict[str, Any],
    settings: Settings,
) -> AsyncIterator[bytes]:
    """Handle the case when all tool calls are duplicates.

    Injects context-aware guidance, retries LLM call, and forces a final answer
    if the LLM keeps describing actions instead of responding.

    Updates state dict with:
    - tool_calls: Next tool calls (list or None)
    - content: Latest content string

    Args:
        duplicate_calls: List of duplicate call info dicts
        messages_for_llm: Current message history (mutated)
        chat_provider: LLM provider instance
        available_tools: Available tools list
        chat_id: Chat ID for logging
        iteration: Current iteration number
        latest_content: Current latest content for fallback
        state: Mutable dict to communicate results back to caller
        settings: Application settings for thinking configuration

    Yields:
        SSE-formatted response chunks

    """
    state["tool_calls"] = None
    state["content"] = latest_content

    logger.warning(
        "chat_stream_all_tools_duplicate",
        chat_id=chat_id,
        iteration=iteration,
        duplicate_count=len(duplicate_calls),
    )

    # Generate context-aware guidance based on what was already found
    dup_names = [d.get("function", {}).get("name", "unknown") for d in duplicate_calls]
    guidance = _generate_duplicate_guidance(messages_for_llm, dup_names)
    messages_for_llm.append({"role": "user", "content": guidance})

    yield format_sse_event(
        "warning",
        {
            "message": "Duplicate tool calls detected. Trying different approach.",
            "duplicates": dup_names,
            "iteration": iteration,
        },
    )

    # Make another LLM call to get a new response
    retry_result = await chat_provider.chat(
        messages=messages_for_llm,
        tools=available_tools,
        stream=True,
        enable_thinking=settings.llm.thinking_for_tools,
    )
    retry_content = ""
    try:
        async for chunk in _process_iteration_stream(
            stream_result=retry_result,
            chat_id=chat_id,
            iteration=iteration,
        ):
            chunk_type = chunk.get("_internal_type")
            if chunk_type == "content":
                yield format_sse_event(
                    "content",
                    {
                        "delta": chunk.get("delta", ""),
                        "accumulated": chunk.get("accumulated", ""),
                    },
                )
                retry_content = chunk.get("accumulated", retry_content)
                state["content"] = retry_content
            elif chunk_type == "done":
                next_tool_calls = chunk.get("tool_calls")
                retry_content = chunk.get("content", retry_content)
                state["content"] = retry_content
                if next_tool_calls:
                    state["tool_calls"] = next_tool_calls
                else:
                    # No tool calls - force final answer if LLM has unfulfilled intent
                    async for event in _force_final_answer_if_intent(
                        retry_content=retry_content,
                        messages_for_llm=messages_for_llm,
                        chat_provider=chat_provider,
                        chat_id=chat_id,
                        iteration=iteration,
                        state=state,
                        settings=settings,
                    ):
                        yield event
                break
    finally:
        await _close_stream(retry_result)


async def _force_final_answer_if_intent(
    retry_content: str,
    messages_for_llm: list[Any],
    chat_provider: Any,
    chat_id: str,
    iteration: int,
    state: dict[str, Any],
    settings: Settings,
) -> AsyncIterator[bytes]:
    """Force a final answer if LLM content contains unfulfilled intent phrases.

    When the LLM says "let me check" or "I'll search" without actually calling tools,
    this makes a no-tools LLM call to force a direct textual answer.

    Updates state["content"] with the forced answer content if applicable.

    Args:
        retry_content: Content from the retry LLM call
        messages_for_llm: Current message history (mutated)
        chat_provider: LLM provider instance
        chat_id: Chat ID for logging
        iteration: Current iteration number
        state: Mutable dict to communicate results back
        settings: Application settings for thinking configuration

    Yields:
        SSE-formatted content chunks from the forced final answer

    """
    content_lower = (retry_content or "").lower()
    if not any(phrase in content_lower for phrase in _INTENT_PHRASES):
        return

    # Skip forcing when content is already a substantive answer
    if len(retry_content or "") > 200:
        logger.info(
            "chat_stream_force_final_skipped_substantial",
            chat_id=chat_id,
            content_length=len(retry_content or ""),
        )
        return

    # LLM is still trying to describe actions - force final answer
    found_nodes = _extract_found_nodes(messages_for_llm)
    if found_nodes:
        node_names = [n["name"] for n in found_nodes[:3]]
        summary_prompt = (
            f"STOP. You've been searching but not completing the task. "
            f"You found these entities: {', '.join(node_names)}. "
            "Based on what you found, provide your final answer NOW. "
            "If you couldn't find a direct connection between them, say so clearly. "
            "Do not describe what you will do - just answer the user's question."
        )
    else:
        summary_prompt = (
            "STOP. Do not describe what you will do. Based on the information "
            "you have already gathered, provide your final answer NOW. "
            "If you couldn't find what the user asked for, say so clearly."
        )

    messages_for_llm.append({"role": "assistant", "content": retry_content})
    messages_for_llm.append({"role": "user", "content": summary_prompt})

    # One more try for final answer (no tools to force text response)
    final_result = await chat_provider.chat(
        messages=messages_for_llm,
        tools=None,
        stream=True,
        enable_thinking=settings.llm.thinking_for_chat,
    )
    try:
        async for final_chunk in _process_iteration_stream(
            stream_result=final_result,
            chat_id=chat_id,
            iteration=iteration,
        ):
            if final_chunk.get("_internal_type") == "content":
                yield format_sse_event(
                    "content",
                    {
                        "delta": final_chunk.get("delta", ""),
                        "accumulated": final_chunk.get("accumulated", ""),
                    },
                )
                state["content"] = final_chunk.get("accumulated", state["content"])
    finally:
        await _close_stream(final_result)


async def _execute_followup_call(
    messages_for_llm: list[Any],
    chat_provider: Any,
    available_tools: list[Any],
    chat_id: str,
    iteration: int,
    state: dict[str, Any],
    settings: Settings,
    llm_debug: Any | None = None,
) -> AsyncIterator[bytes]:
    """Execute follow-up LLM call and stream the response.

    Makes an LLM call after tool execution to check for more tool calls
    or get the final response. Forwards content/thinking chunks to the client.

    Updates state dict with:
    - content: Accumulated content from the follow-up
    - thinking: Thinking content if any
    - tool_calls: Next tool calls (list or None)

    Args:
        messages_for_llm: Current message history
        chat_provider: LLM provider instance
        available_tools: Available tools list
        chat_id: Chat ID for logging
        iteration: Current iteration number
        state: Mutable dict to communicate results back
        settings: Application settings for thinking configuration
        llm_debug: Optional debug info for timing data.

    Yields:
        SSE-formatted response chunks

    """
    logger.info(
        "chat_stream_iteration_followup",
        chat_id=chat_id,
        iteration=iteration,
        message_count=len(messages_for_llm),
        last_message_role=messages_for_llm[-1].get("role") if messages_for_llm else None,
    )

    followup_result = await chat_provider.chat(
        messages=messages_for_llm,
        tools=available_tools,
        stream=True,
        enable_thinking=settings.llm.thinking_for_tools,
    )

    state["content"] = ""
    state["thinking"] = None
    state["tool_calls"] = None
    state["error"] = False

    # Track generation window for the follow-up call
    followup_first_token: float | None = None

    try:
        async for chunk in _process_iteration_stream(
            stream_result=followup_result,
            chat_id=chat_id,
            iteration=iteration,
        ):
            chunk_type = chunk.get("_internal_type")

            if chunk_type == "content":
                followup_first_token = followup_first_token or time.monotonic()
                yield format_sse_event(
                    "content",
                    {
                        "delta": chunk.get("delta", ""),
                        "accumulated": chunk.get("accumulated", ""),
                    },
                )
                state["content"] = chunk.get("accumulated", state["content"])

            elif chunk_type == "thinking":
                state["thinking"] = chunk.get("thinking")
                yield format_sse_event("thinking_delta", {"thinking": state["thinking"]})

            elif chunk_type == "error":
                error_msg = chunk.get("error", "LLM error during follow-up")
                logger.error(
                    "chat_stream_followup_error",
                    chat_id=chat_id,
                    iteration=iteration,
                    error=error_msg,
                )
                state["error"] = True
                yield format_sse_event(
                    "error",
                    {
                        "error": error_msg,
                        "error_code": chunk.get("error_code", "LLM_ERROR"),
                        "error_details": {},
                    },
                )
                break

            elif chunk_type == "done":
                state["content"] = chunk.get("content", state["content"])
                state["thinking"] = chunk.get("thinking", state["thinking"])
                state["tool_calls"] = chunk.get("tool_calls")
                if llm_debug is not None:
                    from chaoscypher_core.streaming.chat.handler import (
                        _save_done_chunk_timing,
                    )

                    _save_done_chunk_timing(llm_debug, chunk)
                    # Save the follow-up call's generation window
                    if followup_first_token is not None:
                        followup_done = time.monotonic()
                        llm_debug.timing["content_generation_ms"] = round(
                            (followup_done - followup_first_token) * 1000
                        )
                break
    finally:
        await _close_stream(followup_result)


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
    has_unfulfilled_intent = any(phrase in content_lower for phrase in _INTENT_PHRASES)

    if not has_unfulfilled_intent or iteration >= MAX_TOOL_ITERATIONS - 1:
        return None

    # Skip retry when the LLM already provided a substantive answer.
    # Short responses like "Let me search for X" are genuine unfulfilled
    # intent, but longer responses with citations or detailed content are
    # real answers that happen to use conversational transition phrases.
    content_len = len(followup_content or "")
    if content_len > 200:
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
    "ToolCallingState",
    "_check_tool_call_limit",
    "_close_stream",
    "_execute_followup_call",
    "_execute_tool_batch",
    "_extract_tool_defaults",
    "_filter_duplicate_tool_calls",
    "_process_iteration_stream",
    "_retry_all_duplicates_path",
    "_retry_unfulfilled_intent",
]
