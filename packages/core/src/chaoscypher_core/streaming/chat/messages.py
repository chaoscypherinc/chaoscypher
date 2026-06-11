# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Message Building and Context Management.

Functions for building the message list sent to LLM providers,
including dynamic context window allocation, token budget estimation,
message format conversion, and debug logging.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.services.chat.engine.constants import SYSTEM_PROMPT
from chaoscypher_core.streaming.chat.utils import (
    get_context_window_for_provider,
    strip_thinking_tags,
)
from chaoscypher_core.utils.tokens import (
    DENSE_CHARS_PER_TOKEN,
    estimate_tokens,
    estimate_tokens_dense,
)


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings

logger = structlog.get_logger(__name__)


# Appended to a tool-result message whose body was head-truncated to keep the
# tool-loop prompt inside the model context window.
TOOL_RESULT_COMPACTION_NOTICE = (
    "\n... [tool result truncated by the system to keep the conversation "
    "within the model's context window]"
)


@dataclass
class ContextInfo:
    """Information about context window usage for frontend display.

    Tracks which messages are included in the LLM context and token usage.
    """

    total_messages: int
    messages_in_context: int
    first_in_context_index: int  # Messages before this index are "out of context"
    tokens_used: int
    tokens_available: int
    context_window: int
    provider: str
    model: str


@dataclass
class MessageBuildResult:
    """Result of building messages for LLM with context info.

    Contains both the messages to send and metadata for the UI.
    """

    messages_for_llm: list[dict[str, Any]]
    context_info: ContextInfo


def _count_tool_call_tokens(tool_calls: list[dict[str, Any]], tool_call_overhead: int) -> int:
    """Count estimated tokens for a list of tool calls.

    Args:
        tool_calls: List of tool call dicts with function name and arguments
        tool_call_overhead: Per-tool-call structural overhead (tokens)

    Returns:
        Estimated token count for all tool calls

    """
    tokens = 0
    for tool_call in tool_calls:
        # Count function name
        func = tool_call.get("function", {})
        name = func.get("name", "")
        if name:
            tokens += estimate_tokens(name)

        # Count arguments (can be large JSON strings — dense content)
        arguments = func.get("arguments", "")
        if arguments:
            if isinstance(arguments, str):
                tokens += estimate_tokens_dense(arguments)
            else:
                # If arguments is a dict, stringify it
                tokens += estimate_tokens_dense(json.dumps(arguments))

        tokens += tool_call_overhead

    return tokens


def _estimate_message_tokens_full(
    msg: dict[str, Any],
    tool_call_overhead: int,
    message_overhead: int,
) -> int:
    """Estimate total tokens for a message including content and tool calls.

    Args:
        msg: Message dictionary with content and optional tool_calls/extra_metadata
        tool_call_overhead: Per-tool-call structural overhead (tokens)
        message_overhead: Per-message structural overhead (tokens)

    Returns:
        Estimated token count for the entire message

    """
    tokens = 0

    # Count content tokens. Tool results are dense JSON and tokenize at
    # ~3 chars/token, not prose's ~4 — under-counting them let oversized
    # tool-loop prompts pass the budget check and silently overflow the
    # context window (live 2026-06-10: 102,168 chars -> >=32,763 tokens).
    content = msg.get("content", "")
    if content:
        if msg.get("role") == "tool":
            tokens += estimate_tokens_dense(content)
        else:
            tokens += estimate_tokens(content)

    # Count tool calls tokens (from extra_metadata for stored messages)
    extra_metadata = msg.get("extra_metadata") or {}
    tool_calls = extra_metadata.get("tool_calls") or msg.get("tool_calls")

    if tool_calls and isinstance(tool_calls, list):
        tokens += _count_tool_call_tokens(tool_calls, tool_call_overhead)

    tokens += message_overhead

    return tokens


def _estimate_context_budget(
    settings: Settings | None,
    system_prompt_tokens: int,
) -> tuple[int, int, str, str, Any]:
    """Estimate the token budget available for chat history.

    Calculates how many tokens can be allocated to conversation messages
    based on the context window, system prompt size, and tool overhead.

    Args:
        settings: Application settings (optional, uses defaults if not provided)
        system_prompt_tokens: Number of tokens in the system prompt

    Returns:
        Tuple of (history_budget, context_window, provider, model, chat_context_settings)

    """
    if settings:
        context_window, provider, model = get_context_window_for_provider(settings)
        cc = settings.chat_context
    else:
        from chaoscypher_core.app_config import ChatContextSettings

        cc = ChatContextSettings()
        context_window, provider, model = cc.default_context_window, "unknown", "unknown"

    history_budget = int(
        (context_window * cc.history_allocation_percent)
        - system_prompt_tokens
        - cc.tools_token_estimate
    )
    history_budget = max(history_budget, cc.min_history_budget_tokens)

    return history_budget, context_window, provider, model, cc


def _collect_messages_within_budget(
    messages: list[dict[str, Any]],
    max_tokens: int,
    estimate_fn: Any,
) -> tuple[list[dict[str, Any]], int, int]:
    """Collect messages from newest to oldest within a token budget.

    Iterates backward through the message list, accumulating messages
    until the budget is exhausted.

    Args:
        messages: Filtered message list in chronological order
        max_tokens: Maximum token budget for history
        estimate_fn: Function to estimate tokens for a single message

    Returns:
        Tuple of (selected_messages, tokens_used, first_in_context_index)

    """
    tokens_used = 0
    selected: list[dict[str, Any]] = []
    first_in_context_index = len(messages)  # Start assuming none included

    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        msg_tokens = estimate_fn(msg)

        # Check if adding this message would exceed budget
        if tokens_used + msg_tokens > max_tokens:
            break

        tokens_used += msg_tokens
        selected.insert(0, msg)  # Prepend to maintain order
        first_in_context_index = i

    return selected, tokens_used, first_in_context_index


def _convert_message_to_llm_format(message: dict[str, Any]) -> dict[str, Any]:
    """Convert a single stored message to the format expected by LLM providers.

    Handles tool messages (with tool_call_id), assistant messages (with optional
    tool_calls and thinking tag removal), and user messages.

    Args:
        message: Stored message dict with role, content, and optional extra_metadata

    Returns:
        Message dict in LLM-compatible format

    """
    if message["role"] == "tool":
        extra_metadata = message.get("extra_metadata") or {}
        return {
            "role": message["role"],
            "content": message["content"],
            "tool_call_id": extra_metadata.get("tool_call_id") or "",
        }

    if message["role"] == "assistant":
        clean_content = strip_thinking_tags(message["content"])
        message_dict: dict[str, Any] = {"role": message["role"], "content": clean_content}
        extra_metadata = message.get("extra_metadata") or {}
        tool_calls = extra_metadata.get("tool_calls")
        if tool_calls:
            message_dict["tool_calls"] = tool_calls
        return message_dict

    clean_content = strip_thinking_tags(message["content"])
    return {"role": message["role"], "content": clean_content}


def build_messages_for_llm(
    chat: dict[str, Any],
    chat_id: str,
    settings: Settings | None = None,
    source_metadata: list[dict[str, Any]] | None = None,
) -> MessageBuildResult:
    """Build message list for LLM from chat history with dynamic context allocation.

    Allocates 50% of context window to chat history (conservative for tool responses).
    Iterates from newest to oldest messages until budget is exhausted.

    Args:
        chat: Chat data with messages
        chat_id: Chat ID for logging
        settings: Application settings (optional, uses defaults if not provided)
        source_metadata: Optional source info for scope-augmented system prompt

    Returns:
        MessageBuildResult with messages and context info for UI

    """
    # Calculate token budgets
    system_prompt_tokens = estimate_tokens(SYSTEM_PROMPT)
    history_budget, context_window, provider, model, cc = _estimate_context_budget(
        settings, system_prompt_tokens
    )
    estimate_message_tokens = partial(
        _estimate_message_tokens_full,
        tool_call_overhead=cc.tool_call_token_overhead,
        message_overhead=cc.message_structure_token_overhead,
    )

    # Build system prompt (with optional source scope context)
    system_content = SYSTEM_PROMPT
    if source_metadata:
        source_lines = "\n".join(f'- "{s["title"]}" ({s["id"]})' for s in source_metadata)
        system_content += (
            "\n\n--- SOURCE SCOPE ---\n"
            "This conversation is scoped to the following source documents:\n"
            f"<source_list>\n{source_lines}\n</source_list>\n\n"
            "All your searches and graph queries are automatically filtered to these sources.\n"
            "You do not have access to data from other sources in this conversation.\n"
            "If you cannot find the answer within these sources, say so — do not speculate."
        )

    # Start with system prompt
    messages_for_llm = [{"role": "system", "content": system_content}]

    # Get all messages and filter out fallback errors
    all_messages = chat.get("messages", [])
    filtered_messages = [
        msg
        for msg in all_messages
        if msg
        and not msg.get("content", "").startswith("I apologize, but I didn't generate a response")
    ]

    # Collect messages within budget (newest to oldest)
    messages_to_include, tokens_used, first_in_context_index = _collect_messages_within_budget(
        filtered_messages, history_budget, estimate_message_tokens
    )

    logger.debug(
        "chat_stream_dynamic_context",
        chat_id=chat_id,
        context_window=context_window,
        history_budget=history_budget,
        total_messages=len(filtered_messages),
        messages_included=len(messages_to_include),
        tokens_used=tokens_used,
        first_in_context_index=first_in_context_index,
    )

    # Convert included messages to LLM format
    messages_for_llm.extend(_convert_message_to_llm_format(msg) for msg in messages_to_include)

    # Build context info for frontend
    context_info = ContextInfo(
        total_messages=len(filtered_messages),
        messages_in_context=len(messages_to_include),
        first_in_context_index=first_in_context_index,
        tokens_used=tokens_used + system_prompt_tokens,
        tokens_available=history_budget,
        context_window=context_window,
        provider=provider,
        model=model,
    )

    return MessageBuildResult(
        messages_for_llm=messages_for_llm,
        context_info=context_info,
    )


def enforce_tool_loop_budget(
    messages_for_llm: list[dict[str, Any]],
    settings: Settings | None,
    chat_id: str,
) -> dict[str, Any] | None:
    """Compact older tool results in place so the prompt fits the context window.

    The tool-calling loop appends large JSON tool results to
    ``messages_for_llm`` every iteration with no per-call budget. Once the
    prompt exceeds the provider context window, Ollama silently truncates the
    HEAD of the prompt (``keep=4``) — the system prompt and the user's
    question are the first content dropped, which degrades multi-hop answers
    to confident 1-2 sentence fragments.

    Compaction head-truncates tool-result message bodies (oldest first) down
    to ``compacted_tool_result_max_chars`` and appends
    :data:`TOOL_RESULT_COMPACTION_NOTICE`. Tool results from the current
    batch (after the last assistant tool_calls message) are only compacted as
    a last resort, since the imminent follow-up call reasons over exactly
    those results.

    Args:
        messages_for_llm: Message list for the next LLM call (mutated in place).
        settings: Application settings (None disables enforcement).
        chat_id: Chat ID for logging.

    Returns:
        Summary dict (tokens_before/tokens_after/budget/context_window/
        compacted_count/still_over_budget) when compaction ran, or None when
        the prompt already fit.

    """
    if settings is None:
        return None

    # Best-effort: budget enforcement must never break the stream itself
    # (e.g. partially faked settings in tests).
    try:
        context_window, provider, model = get_context_window_for_provider(settings)
        cc = settings.chat_context
        budget = context_window - cc.response_token_reserve
        estimate = partial(
            _estimate_message_tokens_full,
            tool_call_overhead=cc.tool_call_token_overhead,
            message_overhead=cc.message_structure_token_overhead,
        )
        tokens_before = sum(estimate(m) for m in messages_for_llm) + cc.tools_token_estimate
        within_budget = tokens_before <= budget
    except (TypeError, AttributeError) as e:
        logger.debug(
            "chat_stream_budget_check_skipped",
            chat_id=chat_id,
            error_type=type(e).__name__,
        )
        return None

    if within_budget:
        return None

    # Tool results after the last assistant tool_calls message belong to the
    # current batch; everything before it is an older, already-reasoned-over
    # result and gets compacted first.
    last_assistant_idx = -1
    for i, msg in enumerate(messages_for_llm):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            last_assistant_idx = i

    max_chars = cc.compacted_tool_result_max_chars
    tokens = tokens_before
    compacted_count = 0

    def _compact(msg: dict[str, Any]) -> int:
        nonlocal compacted_count
        content = msg.get("content") or ""
        if len(content) <= max_chars + len(TOOL_RESULT_COMPACTION_NOTICE):
            return 0
        before = estimate(msg)
        msg["content"] = content[:max_chars] + TOOL_RESULT_COMPACTION_NOTICE
        compacted_count += 1
        return before - estimate(msg)

    # Pass 1: older tool results, oldest first.
    for i, msg in enumerate(messages_for_llm):
        if tokens <= budget:
            break
        if msg.get("role") == "tool" and i < last_assistant_idx:
            tokens -= _compact(msg)

    # Pass 2: current-batch tool results, only if still over budget. The
    # imminent follow-up call reasons over exactly these results, so trim
    # only what the deficit requires (largest first) instead of slashing
    # every result to the floor — a 70K-char graphrag result a few hundred
    # tokens over budget should lose a few hundred tokens, not 97% of it.
    if tokens > budget:
        current_batch = [
            m
            for i, m in enumerate(messages_for_llm)
            if m.get("role") == "tool" and i > last_assistant_idx
        ]
        current_batch.sort(key=lambda m: len(m.get("content") or ""), reverse=True)
        for msg in current_batch:
            if tokens <= budget:
                break
            content = msg.get("content") or ""
            before = estimate(msg)
            # Token room left for this message if everything else stays.
            allowed = budget - (tokens - before)
            target = (
                (allowed - cc.message_structure_token_overhead) * DENSE_CHARS_PER_TOKEN
                - len(TOOL_RESULT_COMPACTION_NOTICE)
                - DENSE_CHARS_PER_TOKEN  # floor-rounding slack
            )
            target = max(max_chars, target)
            if target >= len(content):
                continue
            msg["content"] = content[:target] + TOOL_RESULT_COMPACTION_NOTICE
            compacted_count += 1
            tokens -= before - estimate(msg)

    summary = {
        "tokens_before": tokens_before,
        "tokens_after": tokens,
        "budget": budget,
        "context_window": context_window,
        "compacted_count": compacted_count,
        "still_over_budget": tokens > budget,
    }
    logger.warning(
        "chat_stream_tool_loop_compacted",
        chat_id=chat_id,
        provider=provider,
        model=model,
        **summary,
    )
    return summary


def detect_truncation_warnings(
    done_chunk: dict[str, Any],
    settings: Settings | None,
    chat_id: str,
) -> list[dict[str, str]]:
    """Detect silent truncation signals on a provider ``done`` chunk.

    Two independent signals, both verified against Ollama behavior:

    - ``finish_reason == "length"`` — generation stopped because the output
      token budget (or the remaining context room) ran out; the visible
      answer is cut off mid-thought.
    - ``usage.prompt_tokens`` pinned at the context window — Ollama silently
      truncates an oversized prompt (server log: ``truncating input prompt``)
      and reports ``prompt_eval_count == num_ctx - 1``; the count is the only
      response-level signal that instructions/history were dropped.

    Args:
        done_chunk: Provider done chunk with finish_reason/usage fields.
        settings: Application settings (None skips the overflow check).
        chat_id: Chat ID for logging.

    Returns:
        List of ``{"kind", "message"}`` warning dicts (possibly empty),
        ordered output_truncated first.

    """
    warnings: list[dict[str, str]] = []

    if done_chunk.get("finish_reason") == "length":
        warnings.append(
            {
                "kind": "output_truncated",
                "message": (
                    "The model ran out of room and this answer was cut off. "
                    "Increase the model's context window or output token limit "
                    "in Settings, or ask a narrower question."
                ),
            }
        )

    usage = done_chunk.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens") or 0
    if settings is not None and prompt_tokens:
        # Best-effort diagnostics: a malformed usage payload or partially
        # faked settings must never break the stream itself.
        try:
            context_window, provider, _model = get_context_window_for_provider(settings)
            margin = settings.chat_context.context_overflow_warning_margin
            overflowed = prompt_tokens >= context_window - margin
        except (TypeError, AttributeError) as e:
            logger.debug(
                "chat_stream_overflow_check_skipped",
                chat_id=chat_id,
                error_type=type(e).__name__,
            )
            overflowed = False
        if overflowed:
            hint = (
                "Increase the Ollama context window (Settings → LLM → context size)"
                if provider == "ollama"
                else "Increase the model's context window in Settings → LLM"
            )
            warnings.append(
                {
                    "kind": "context_overflow",
                    "message": (
                        f"The conversation and tool results filled the model's "
                        f"context window ({prompt_tokens:,} of {context_window:,} "
                        "tokens). Earlier context — including instructions and "
                        "your question — may have been dropped, degrading this "
                        f"answer. {hint}, or start a new chat."
                    ),
                }
            )

    if warnings:
        logger.warning(
            "chat_stream_truncation_detected",
            chat_id=chat_id,
            kinds=[w["kind"] for w in warnings],
            finish_reason=done_chunk.get("finish_reason"),
            prompt_tokens=prompt_tokens,
        )
    return warnings


def log_messages_debug(
    messages_for_llm: list[dict[str, Any]],
    chat: dict[str, Any],
    chat_id: str,
) -> None:
    """Log detailed debug information about messages.

    Args:
        messages_for_llm: Messages prepared for LLM
        chat: Original chat data
        chat_id: Chat ID for logging

    """
    # Message type breakdown
    message_types: dict[str, int] = {}
    for msg in messages_for_llm:
        role = msg.get("role", "unknown")
        message_types[role] = message_types.get(role, 0) + 1

    logger.debug(
        "chat_stream_request_payload",
        chat_id=chat_id,
        chat_message_number=len(chat["messages"]),
        total_messages=len(messages_for_llm),
        message_breakdown=message_types,
    )

    # Individual message inspection
    preview_chars = get_settings().chat.log_message_preview_chars
    for idx, msg in enumerate(messages_for_llm):
        content = msg.get("content", "")
        content_preview = content[:preview_chars] if content else "[empty]"
        if len(content) > preview_chars:
            content_preview += f"... ({len(content)} chars total)"

        logger.debug(
            "chat_stream_message_inspection",
            chat_id=chat_id,
            message_index=idx + 1,
            total_messages=len(messages_for_llm),
            role=msg.get("role", "unknown"),
            content_preview=content_preview,
        )

        # Metadata fields
        metadata_info: dict[str, int | str] = {}
        if "thinking" in msg:
            metadata_info["thinking_length"] = len(msg["thinking"])
        if "tool_calls" in msg:
            tool_call_count = len(msg["tool_calls"]) if isinstance(msg["tool_calls"], list) else 1
            metadata_info["tool_call_count"] = tool_call_count
        if "tool_call_id" in msg:
            metadata_info["tool_call_id"] = msg["tool_call_id"]
        if "name" in msg:
            metadata_info["name"] = msg["name"]

        if metadata_info:
            logger.debug(
                "chat_stream_message_metadata",
                chat_id=chat_id,
                message_index=idx + 1,
                **metadata_info,
            )

        # Full structure for assistant messages
        if msg.get("role") == "assistant":
            logger.debug(
                "chat_stream_assistant_message_structure",
                chat_id=chat_id,
                message_index=idx + 1,
                full_structure=msg,
            )

    # Token estimate
    total_chars = sum(len(str(msg.get("content", ""))) for msg in messages_for_llm)
    estimated_tokens = total_chars // 4
    logger.debug(
        "chat_stream_token_estimate",
        chat_id=chat_id,
        estimated_tokens=estimated_tokens,
        total_chars=total_chars,
    )


__all__ = [
    "TOOL_RESULT_COMPACTION_NOTICE",
    "ContextInfo",
    "MessageBuildResult",
    "build_messages_for_llm",
    "detect_truncation_warnings",
    "enforce_tool_loop_budget",
    "log_messages_debug",
]
