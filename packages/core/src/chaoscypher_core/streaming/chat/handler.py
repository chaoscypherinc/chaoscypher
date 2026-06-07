# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SSE Streaming Chat Handler.

Main orchestration for streaming chat responses with tool calling.
Provides the top-level ``stream_chat_response`` generator and the
LLM stream processing, response finalization, timing, and token
tracking pipeline.
"""

from __future__ import annotations

import asyncio
import copy
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import QUEUE_LLM
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.queue import queue_client
from chaoscypher_core.services.chat.engine.constants import MAX_TOOL_ITERATIONS
from chaoscypher_core.services.workflows.tools import get_tool_discovery
from chaoscypher_core.streaming.chat.citations import (
    _strip_blockquotes_before_citations,
    _strip_inline_quotes_before_citations,
    correct_mismatched_citations,
    enrich_chunk_citations_from_tool_results,
    enrich_entity_references_from_tool_results,
    extract_chunk_citations,
    extract_entity_references,
    inject_citations_for_uncited_paragraphs,
    inject_citations_into_blockquotes,
    normalize_chunk_references,
    strip_duplicated_citation_text,
)
from chaoscypher_core.streaming.chat.messages import (
    build_messages_for_llm,
    log_messages_debug,
)
from chaoscypher_core.streaming.chat.tools import (
    ToolCallingState,
    _check_tool_call_limit,
    _close_stream,
    _execute_followup_call,
    _execute_tool_batch,
    _extract_tool_defaults,
    _filter_duplicate_tool_calls,
    _retry_all_duplicates_path,
    _retry_unfulfilled_intent,
)
from chaoscypher_core.streaming.chat.utils import (
    create_fallback_response,
    extract_thinking_from_tags,
    format_sse_event,
    get_model_name,
    setup_chat_providers,
    strip_thinking_tags,
)
from chaoscypher_core.streaming.chat.validation import (
    validate_citation_references,
    validate_response_grounding,
)
from chaoscypher_core.utils.tokens import estimate_message_tokens, estimate_tokens


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings

logger = structlog.get_logger(__name__)


@dataclass
class LLMDebugInfo:
    """Debug information about LLM request/response for advanced UI display.

    Captures the raw input/output of LLM calls for debugging and transparency.
    """

    provider: str
    model: str
    initial_messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    final_messages: list[dict[str, Any]] = field(default_factory=list)
    response_content: str = ""
    tool_calls_made: int = 0
    iterations: int = 0
    timing: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "provider": self.provider,
            "model": self.model,
            "initial_messages": self.initial_messages,
            "tools": self.tools,
            "final_messages": self.final_messages,
            "response_content": self.response_content,
            "tool_calls_made": self.tool_calls_made,
            "iterations": self.iterations,
            "timing": self.timing,
        }


def _spend_check(settings: Settings) -> None:
    """Enforce the daily/source LLM spend cap before a streaming chat turn.

    Raises :class:`LLMSpendCapExceededError` (an ``LLMError``, surfaced to the
    client as an SSE ``error`` event by the handler's ``except LLMError`` arm)
    when a configured cap is reached. Opens and closes a short-lived adapter on
    the active database's ``app.db``, mirroring the queued chat path so the
    interactive streaming path honours the same per-day budget.
    """
    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
    from chaoscypher_core.services.llm.spend import get_llm_spend_tracker

    db_name = settings.current_database
    adapter = get_sqlite_adapter(db_name)
    try:
        get_llm_spend_tracker().check_and_raise(
            source_id=None,
            settings=settings,
            adapter=adapter,
            database_name=db_name,
        )
    finally:
        adapter.disconnect()


def _spend_record(settings: Settings, total_tokens: int) -> None:
    """Add a completed streaming turn's tokens to the persisted daily total.

    Best-effort: a storage failure is swallowed by the caller and never breaks
    the just-completed chat turn.
    """
    if total_tokens <= 0:
        return
    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
    from chaoscypher_core.services.llm.spend import get_llm_spend_tracker

    db_name = settings.current_database
    adapter = get_sqlite_adapter(db_name)
    try:
        get_llm_spend_tracker().record(
            None,
            total_tokens,
            adapter=adapter,
            database_name=db_name,
        )
    finally:
        adapter.disconnect()


async def stream_chat_response(
    chat_id: str,
    user_message: str,
    chat_service: Any,
    graph_manager: Any,
    search_manager: Any,
    config_manager: Any,
    settings: Settings,
    indexing_manager: Any = None,
    source_storage: Any = None,
) -> AsyncIterator[bytes]:
    """Stream chat response with SSE formatting and tool calling.

    Args:
        chat_id: ID of the chat
        user_message: User's message content
        chat_service: ChatService instance
        graph_manager: GraphRepository instance
        search_manager: SearchRepository instance
        config_manager: ConfigManager instance
        settings: Settings instance
        indexing_manager: Optional IndexingProtocol for chunk operations
        source_storage: Optional SourceStorageProtocol for citation lookups

    Yields:
        SSE-formatted chat response chunks

    Note:
        If the client disconnects mid-stream, the underlying
        ``EventSourceResponse`` cancels the generator and work IS LOST.
        Clients needing durable chat must use ``POST /chats/{id}/send``
        plus ``GET /chats/{id}/events`` — those queue the LLM work on the
        background worker and the result survives disconnect.

    """
    logger.info("chat_stream_started", chat_id=chat_id)
    stream_start = time.monotonic()
    try:
        # Validate and setup chat
        chat = chat_service.get_chat(chat_id)
        if not chat:
            yield format_sse_event("error", {"error": "Chat not found"})
            return

        chat_service.update_chat_status(chat_id, "processing")
        chat_service.add_message(chat_id=chat_id, role="user", content=user_message)

        # Reload chat to include new message
        chat = chat_service.get_chat(chat_id)
        if not chat:
            yield format_sse_event("error", {"error": "Failed to reload chat"})
            return

        # Resolve source scope
        source_ids = chat.get("source_ids")
        source_metadata = None
        if source_ids and indexing_manager:
            source_metadata = []
            for sid in source_ids:
                source = indexing_manager.get_source(sid, settings.current_database)
                if source:
                    source_metadata.append(
                        {
                            "id": sid,
                            "title": source.get("title", source.get("filename", sid)),
                        }
                    )

        # Setup providers and tools
        chat_provider, tool_executor, available_tools = setup_chat_providers(
            settings,
            graph_manager,
            search_manager,
            chat_id,
            indexing_manager,
            source_ids=source_ids,
            source_storage=source_storage,
        )

        # Warm the tool discovery singleton (ensures plugins are discovered)
        get_tool_discovery()

        # Build messages for LLM
        build_result = build_messages_for_llm(
            chat, chat_id, settings, source_metadata=source_metadata
        )
        messages_for_llm = build_result.messages_for_llm
        context_info = build_result.context_info

        # Log configuration
        provider_name = settings.llm.chat_provider.lower()
        model_name = get_model_name(settings)
        logger.info(
            "chat_stream_llm_config",
            chat_id=chat_id,
            provider=provider_name,
            model=model_name,
            thinking_for_chat=settings.llm.thinking_for_chat,
            thinking_for_tools=settings.llm.thinking_for_tools,
        )
        logger.info(
            "chat_stream_messages_prepared",
            chat_id=chat_id,
            message_count=len(messages_for_llm),
            tool_count=len(available_tools) if available_tools else 0,
        )

        # Debug logging
        log_messages_debug(messages_for_llm, chat, chat_id)

        # Send context info to frontend
        yield format_sse_event(
            "context_info",
            {
                "total_messages": context_info.total_messages,
                "messages_in_context": context_info.messages_in_context,
                "first_in_context_index": context_info.first_in_context_index,
                "tokens_used": context_info.tokens_used,
                "tokens_available": context_info.tokens_available,
                "context_window": context_info.context_window,
                "provider": context_info.provider,
                "model": context_info.model,
            },
        )

        # Create LLM debug info for advanced UI display
        llm_debug = LLMDebugInfo(
            provider=provider_name,
            model=model_name,
            initial_messages=copy.deepcopy(messages_for_llm),
            tools=available_tools or [],
        )

        # First LLM call
        enable_thinking = settings.llm.thinking_for_chat
        logger.info(
            "chat_stream_thinking_mode_set", chat_id=chat_id, enable_thinking=enable_thinking
        )

        # Enforce the daily/source spend cap before spending tokens on this
        # turn. LLMSpendCapExceededError is an LLMError, so the except LLMError
        # arm below surfaces it to the client as an SSE error event and stops.
        # Offloaded to a thread: the check opens an adapter and runs blocking
        # SQLite (incl. SafeSession busy-retry time.sleep) that must not stall
        # the Cortex event loop / other in-flight SSE streams.
        await asyncio.to_thread(_spend_check, settings)

        llm_result = await chat_provider.chat(
            messages=messages_for_llm,
            tools=available_tools,
            stream=True,
            enable_thinking=enable_thinking,
        )
        logger.info("chat_stream_llm_call_initiated", chat_id=chat_id)

        # Store stream_start on llm_debug for timing calculations downstream
        if llm_debug:
            llm_debug.timing["_stream_start"] = stream_start

        # Process streaming response
        async for chunk in _process_llm_stream(
            llm_result=llm_result,
            chat_id=chat_id,
            chat_service=chat_service,
            chat_provider=chat_provider,
            tool_executor=tool_executor,
            available_tools=available_tools,
            messages_for_llm=messages_for_llm,
            llm_debug=llm_debug,
            settings=settings,
        ):
            yield chunk

    except LLMError as e:
        logger.exception(
            "chat_stream_llm_error",
            chat_id=chat_id,
            error_type=type(e).__name__,
            error_code=e.code,
            error_message=e.message,
            provider=e.provider,
            is_retryable=e.is_retryable,
        )
        chat_service.update_chat_status(chat_id, "error")
        yield format_sse_event(
            "error",
            {
                "error": e.message,
                "error_code": e.code,
                "error_details": e.details,
            },
        )
    except Exception as e:
        logger.exception(
            "chat_stream_error",
            chat_id=chat_id,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        chat_service.update_chat_status(chat_id, "error")
        yield format_sse_event(
            "error",
            {
                "error": "An unexpected error occurred",
                "error_code": "UNKNOWN_ERROR",
                "error_details": {},
            },
        )


def _save_stream_timing(
    llm_debug: LLMDebugInfo | None,
    stream_start: float | None,
    first_token_time: float | None,
    thinking_start: float | None,
    thinking_end: float | None,
    *,
    has_tool_calls: bool = False,
) -> None:
    """Store first-token and thinking timing on the debug info object.

    For non-tool-call responses, also records the content generation window
    (first content token to now) so ``_compute_final_timing`` can compute
    accurate tokens/sec without subtracting from total wall-clock time.

    Args:
        llm_debug: Debug info accumulator (mutated in place).
        stream_start: Monotonic timestamp when stream started.
        first_token_time: Monotonic timestamp of first content token.
        thinking_start: Monotonic timestamp when thinking began.
        thinking_end: Monotonic timestamp when thinking ended.
        has_tool_calls: Whether this stream produced tool calls.

    """
    if not llm_debug or stream_start is None:
        return
    if first_token_time is not None:
        llm_debug.timing["time_to_first_token_ms"] = round((first_token_time - stream_start) * 1000)
    if thinking_start is not None and thinking_end is not None:
        llm_debug.timing["thinking_ms"] = round((thinking_end - thinking_start) * 1000)
    # For direct responses (no tool calls), save generation window
    if not has_tool_calls and first_token_time is not None:
        llm_debug.timing["content_generation_ms"] = round(
            (time.monotonic() - first_token_time) * 1000
        )


def _save_done_chunk_timing(llm_debug: LLMDebugInfo | None, chunk: dict[str, Any]) -> None:
    """Capture provider-native timings and usage from a streaming done chunk.

    Each call overwrites previous values so the last iteration wins.

    Args:
        llm_debug: Debug info accumulator (mutated in place).
        chunk: The ``done`` chunk from the LLM provider stream.

    """
    if llm_debug is None:
        return
    provider_timings = chunk.get("provider_timings")
    if provider_timings:
        llm_debug.timing["provider_timings"] = provider_timings
    chunk_usage = chunk.get("usage")
    if chunk_usage:
        llm_debug.timing["usage"] = chunk_usage


def _build_timing_update_event(llm_debug: LLMDebugInfo | None) -> bytes | None:
    """Build a timing_update SSE event if thinking timing is available.

    Args:
        llm_debug: Debug info containing timing data.

    Returns:
        SSE-formatted bytes or None if no timing data.

    """
    if not llm_debug or llm_debug.timing.get("thinking_ms") is None:
        return None
    return format_sse_event(
        "timing_update",
        {"thinking_ms": llm_debug.timing["thinking_ms"]},
    )


def _compute_final_timing(
    llm_debug: LLMDebugInfo | None,
    clean_content: str,
) -> None:
    """Compute total, generation, and throughput timing on the debug info object.

    Uses provider-native timing when available (e.g. Ollama ``eval_duration``),
    falling back to an improved wall-clock estimate for providers that don't
    report server-side generation time.

    Args:
        llm_debug: Debug info accumulator (mutated in place).
        clean_content: Final cleaned response content for token estimation.

    """
    if not llm_debug:
        return
    stream_start = llm_debug.timing.pop("_stream_start", None)
    if stream_start is None:
        return

    total_ms = round((time.monotonic() - stream_start) * 1000)
    llm_debug.timing["total_ms"] = total_ms

    # Pop transient data used only for computation
    provider_timings = llm_debug.timing.pop("provider_timings", None)
    usage = llm_debug.timing.pop("usage", None)

    # Path A: Provider-native timing (Ollama eval_duration)
    # NOTE: eval_count includes ALL generated tokens (thinking + content).
    # tok/s reflects true GPU throughput; output_tokens may exceed visible content.
    eval_duration_ns = (provider_timings or {}).get("eval_duration_ns")
    eval_count = (provider_timings or {}).get("eval_count")
    if eval_duration_ns and eval_count:
        eval_duration_ms = eval_duration_ns / 1_000_000
        llm_debug.timing["output_tokens"] = eval_count
        llm_debug.timing["generation_ms"] = round(eval_duration_ms)
        llm_debug.timing["tokens_per_sec"] = round(
            eval_count / (eval_duration_ns / 1_000_000_000), 1
        )
        llm_debug.timing["native_timing"] = True
        return

    # Path B: Wall-clock fallback
    # Prefer actual completion_tokens from provider usage over heuristic
    completion_tokens = (usage or {}).get("completion_tokens")
    output_tokens = completion_tokens if completion_tokens else estimate_tokens(clean_content)
    llm_debug.timing["output_tokens"] = output_tokens

    # Best source: follow-up call's generation window (first content token -> done)
    # This directly measures token generation, excluding TTFT, thinking, tool execution
    followup_gen_ms = llm_debug.timing.pop("content_generation_ms", None)
    if followup_gen_ms and followup_gen_ms > 0 and output_tokens > 0:
        llm_debug.timing["generation_ms"] = followup_gen_ms
        llm_debug.timing["tokens_per_sec"] = round(output_tokens / (followup_gen_ms / 1000), 1)
        llm_debug.timing["native_timing"] = False
        return

    # Fallback: subtract known non-generation time from total
    generation_time = total_ms - llm_debug.timing.get("thinking_ms", 0)
    tool_total_ms = sum(t.get("duration_ms", 0) for t in llm_debug.timing.get("tool_calls", []))
    generation_time -= tool_total_ms
    ttft_ms = llm_debug.timing.get("time_to_first_token_ms", 0)
    generation_time -= ttft_ms

    if generation_time > 0 and output_tokens > 0:
        llm_debug.timing["generation_ms"] = round(generation_time)
        llm_debug.timing["tokens_per_sec"] = round(output_tokens / (generation_time / 1000), 1)
        llm_debug.timing["native_timing"] = False


async def _process_llm_stream(
    llm_result: Any,
    chat_id: str,
    chat_service: Any,
    chat_provider: Any,
    tool_executor: Any,
    available_tools: list[Any],
    messages_for_llm: list[Any],
    llm_debug: LLMDebugInfo | None = None,
    settings: Settings | None = None,
) -> AsyncIterator[bytes]:
    """Process the LLM streaming response.

    Args:
        llm_result: Streaming result from LLM
        chat_id: Chat ID for logging
        chat_service: ChatService instance
        chat_provider: LLM provider instance
        tool_executor: ToolExecutorService instance
        available_tools: Available tools list
        messages_for_llm: Current message history
        llm_debug: Optional debug info for advanced UI display
        settings: Optional application settings (for validation toggle)

    Yields:
        SSE-formatted response chunks

    """
    if not hasattr(llm_result, "__aiter__"):
        logger.warning("chat_stream_not_iterable", chat_id=chat_id)
        return

    logger.debug("chat_stream_iteration_started", chat_id=chat_id)
    accumulated_content = ""
    thinking = None
    tool_calls = None
    chunk_count = 0

    # Timing tracking
    stream_start = llm_debug.timing.get("_stream_start") if llm_debug else None
    first_token_time: float | None = None
    thinking_start: float | None = None
    thinking_end: float | None = None

    stream_error_occurred = False
    try:
        async for chunk in llm_result:
            chunk_count += 1
            chunk_type = chunk.get("type")
            logger.debug(
                "chat_stream_chunk_received",
                chat_id=chat_id,
                chunk_number=chunk_count,
                chunk_type=chunk_type,
            )

            if chunk_type == "content":
                first_token_time = first_token_time or time.monotonic()
                delta = chunk.get("delta", "")
                accumulated_content = chunk.get("accumulated", accumulated_content)
                yield format_sse_event(
                    "content", {"delta": delta, "accumulated": accumulated_content}
                )

            elif chunk_type == "thinking_delta":
                thinking_start = thinking_start or time.monotonic()
                thinking_end = time.monotonic()
                thinking = chunk.get("accumulated", "")
                yield format_sse_event("thinking_delta", {"thinking": thinking})

            elif chunk_type == "error":
                error_msg = chunk.get("error", "Unknown LLM error")
                error_code = chunk.get("error_code", "LLM_ERROR")
                error_details = chunk.get("error_details", {})
                logger.error(
                    "chat_stream_provider_error",
                    chat_id=chat_id,
                    error=error_msg,
                    error_code=error_code,
                )
                chat_service.update_chat_status(chat_id, "error")
                yield format_sse_event(
                    "error",
                    {
                        "error": error_msg,
                        "error_code": error_code,
                        "error_details": error_details,
                    },
                )
                stream_error_occurred = True
                break  # Exit loop but still close stream in finally

            elif chunk_type == "done":
                accumulated_content = chunk.get("content", accumulated_content)
                thinking = chunk.get("thinking", thinking)
                tool_calls = chunk.get("tool_calls")
                _save_done_chunk_timing(llm_debug, chunk)
                logger.info(
                    "chat_stream_done",
                    chat_id=chat_id,
                    content_length=len(accumulated_content) if accumulated_content else 0,
                    thinking_length=len(thinking) if thinking else 0,
                    tool_call_count=len(tool_calls) if tool_calls else 0,
                )
                break

        logger.debug("chat_stream_iteration_complete", chat_id=chat_id, total_chunks=chunk_count)

        # Save timing data collected during streaming
        _save_stream_timing(
            llm_debug,
            stream_start,
            first_token_time,
            thinking_start,
            thinking_end,
            has_tool_calls=bool(tool_calls),
        )

    except Exception as iter_error:
        logger.exception(
            "chat_stream_iteration_error",
            chat_id=chat_id,
            error_type=type(iter_error).__name__,
            error_message=str(iter_error),
        )
    finally:
        # Ensure stream is closed even if exception occurs (prevents semaphore leak)
        await _close_stream(llm_result)
        logger.debug("chat_stream_generator_closed", chat_id=chat_id)

    # If an error occurred during streaming, don't continue to tool handling
    if stream_error_occurred:
        return

    # Emit timing_update so frontend can show thinking duration
    timing_event = _build_timing_update_event(llm_debug)
    if timing_event is not None:
        yield timing_event

    # Handle tool calls or finalize
    if tool_calls:
        async for chunk in _handle_tool_calls(
            tool_calls=tool_calls,
            accumulated_content=accumulated_content,
            thinking=thinking,
            chat_id=chat_id,
            chat_service=chat_service,
            chat_provider=chat_provider,
            tool_executor=tool_executor,
            available_tools=available_tools,
            messages_for_llm=messages_for_llm,
            llm_debug=llm_debug,
            settings=settings,
        ):
            yield chunk
    else:
        async for chunk in _finalize_response(
            accumulated_content=accumulated_content,
            thinking=thinking,
            chat_id=chat_id,
            chat_service=chat_service,
            messages_for_llm=messages_for_llm,
            llm_debug=llm_debug,
            tools_were_available=bool(available_tools),
            settings=settings,
        ):
            yield chunk


async def _handle_tool_calls(  # noqa: C901, PLR0912
    tool_calls: list[Any],
    accumulated_content: str,
    thinking: str | None,
    chat_id: str,
    chat_service: Any,
    chat_provider: Any,
    tool_executor: Any,
    available_tools: list[Any],
    messages_for_llm: list[Any],
    llm_debug: LLMDebugInfo | None = None,
    settings: Settings | None = None,
) -> AsyncIterator[bytes]:
    """Handle tool call execution with multi-step iteration support.

    Supports multiple rounds of tool calling for complex queries that require
    chaining multiple tools (e.g., "find Pierre" -> "find Andrei" ->
    "find connections").

    Args:
        tool_calls: List of tool calls to execute
        accumulated_content: Content accumulated so far
        thinking: Thinking content if any
        chat_id: Chat ID for logging
        chat_service: ChatService instance
        chat_provider: LLM provider instance
        tool_executor: ToolExecutorService instance
        available_tools: Available tools list
        messages_for_llm: Current message history
        llm_debug: Optional debug info for LLM calls
        settings: Optional application settings (for validation toggle)

    Yields:
        SSE-formatted response chunks

    """
    state = ToolCallingState(
        current_tool_calls=tool_calls,
        current_content=accumulated_content,
        current_thinking=thinking,
        latest_content=accumulated_content,
        all_thinking_parts=[thinking] if thinking else [],
    )
    # Build schema defaults map for signature normalization.
    tool_defaults = _extract_tool_defaults(available_tools) if available_tools else None
    logger.debug(
        "chat_stream_tool_defaults_extracted",
        chat_id=chat_id,
        tool_defaults=tool_defaults,
    )

    # Send initial thinking if available.
    if state.current_thinking:
        yield format_sse_event("thinking", {"thinking": state.current_thinking})

    while state.current_tool_calls and state.iteration < MAX_TOOL_ITERATIONS:
        state.iteration += 1
        batch_size = len(state.current_tool_calls)
        state.total_tool_calls += batch_size

        logger.info(
            "chat_stream_tool_iteration_started",
            chat_id=chat_id,
            iteration=state.iteration,
            tool_count=batch_size,
            total_tool_calls=state.total_tool_calls,
        )

        # Send iteration progress event for UI.
        yield format_sse_event(
            "iteration_progress",
            {
                "iteration": state.iteration,
                "tool_count": batch_size,
                "total_tool_calls": state.total_tool_calls,
            },
        )

        # Check total tool call limit.
        limit_event = _check_tool_call_limit(state.total_tool_calls, chat_id, state.iteration)
        if limit_event is not None:
            yield limit_event
            break

        # Filter out duplicate tool calls (same tool + same arguments).
        filtered_tool_calls, duplicate_calls = _filter_duplicate_tool_calls(
            state.current_tool_calls,
            state.executed_tool_signatures,
            chat_id,
            state.iteration,
            tool_defaults=tool_defaults,
        )

        # Emit cached_tool_calls SSE event for any duplicates found.
        if duplicate_calls:
            state.all_cached_tool_calls.extend(duplicate_calls)
            yield format_sse_event(
                "cached_tool_calls",
                {"tool_calls": duplicate_calls, "iteration": state.iteration},
            )

        # If all calls are duplicates, inject guidance and retry.
        if duplicate_calls and not filtered_tool_calls:
            retry_state: dict[str, Any] = {}
            async for event in _retry_all_duplicates_path(
                duplicate_calls=duplicate_calls,
                messages_for_llm=messages_for_llm,
                chat_provider=chat_provider,
                available_tools=available_tools,
                chat_id=chat_id,
                iteration=state.iteration,
                latest_content=state.latest_content,
                state=retry_state,
                settings=settings,
            ):
                yield event
            state.latest_content = retry_state.get("content", state.latest_content)
            state.current_tool_calls = retry_state.get("tool_calls")
            if state.current_tool_calls:
                state.current_content = state.latest_content
            continue

        # Use filtered tool calls (always non-empty here since the
        # all-duplicates case was handled above).
        state.current_tool_calls = filtered_tool_calls

        # Execute tool batch: notify, save messages, run tools.
        async for event in _execute_tool_batch(
            tool_calls=state.current_tool_calls,
            current_content=state.current_content,
            current_thinking=state.current_thinking,
            chat_id=chat_id,
            chat_service=chat_service,
            tool_executor=tool_executor,
            messages_for_llm=messages_for_llm,
            executed_tool_signatures=state.executed_tool_signatures,
            iteration=state.iteration,
            tool_defaults=tool_defaults,
            llm_debug=llm_debug,
        ):
            yield event

        # Follow-up LLM call to check for more tool calls or final response.
        followup_state: dict[str, Any] = {}
        async for event in _execute_followup_call(
            messages_for_llm=messages_for_llm,
            chat_provider=chat_provider,
            available_tools=available_tools,
            chat_id=chat_id,
            iteration=state.iteration,
            state=followup_state,
            settings=settings,
            llm_debug=llm_debug,
        ):
            yield event

        # If follow-up call errored, stop the tool loop -- error already sent.
        if followup_state.get("error"):
            state.current_tool_calls = None
            state.error_occurred = True
            break

        followup_content = followup_state.get("content", "")
        followup_thinking = followup_state.get("thinking")
        next_tool_calls = followup_state.get("tool_calls")

        # Always track the latest content and accumulate thinking.
        if followup_content:
            state.latest_content = followup_content
            state.all_thinking_parts += [followup_thinking] if followup_thinking else []

        # Update for next iteration or finalize.
        if next_tool_calls:
            logger.info(
                "chat_stream_more_tools_requested",
                chat_id=chat_id,
                iteration=state.iteration,
                next_tool_count=len(next_tool_calls),
            )
            state.current_tool_calls = next_tool_calls
            state.current_content = followup_content
            state.current_thinking = followup_thinking
        else:
            # Check if LLM promised to do something but didn't emit tool calls.
            retry_tool_calls = await _retry_unfulfilled_intent(
                followup_content=followup_content,
                iteration=state.iteration,
                messages_for_llm=messages_for_llm,
                chat_provider=chat_provider,
                available_tools=available_tools,
                chat_id=chat_id,
                settings=settings,
            )
            if retry_tool_calls:
                state.current_tool_calls = retry_tool_calls
                state.current_content = followup_content
                continue

            # No more tool calls -- this is our final response.
            state.current_tool_calls = None

    # Log completion.
    logger.info(
        "chat_stream_tool_iterations_complete",
        chat_id=chat_id,
        total_iterations=state.iteration,
        total_tool_calls=state.total_tool_calls,
        has_final_content=bool(state.latest_content),
        error_occurred=state.error_occurred,
        final_content_preview=state.latest_content[:200] if state.latest_content else "",
    )

    # If an error was already sent to the client, update status and stop.
    if state.error_occurred:
        chat_service.update_chat_status(chat_id, "error")
        return

    # Finalize response (join all thinking with separator for multi-step display).
    finalize_chunk: bytes
    async for finalize_chunk in _finalize_tool_response(
        final_content=state.latest_content,
        final_thinking=(
            "\n\n---\n\n".join(state.all_thinking_parts) if state.all_thinking_parts else None
        ),
        chat_id=chat_id,
        chat_service=chat_service,
        chat_provider=chat_provider,
        messages_for_llm=messages_for_llm,
        iteration=state.iteration,
        llm_debug=llm_debug,
        settings=settings,
        all_cached_tool_calls=state.all_cached_tool_calls,
    ):
        yield finalize_chunk


def _process_response_content(
    content: str,
    tool_results: list[dict[str, Any]],
    chat_id: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Clean content and extract entity/chunk references.

    Strips thinking tags, normalizes chunk aliases, injects citations
    into uncited blockquotes, and extracts entity + chunk references.

    Args:
        content: Raw LLM response.
        tool_results: Tool result messages for enrichment.
        chat_id: Chat ID for logging.

    Returns:
        Tuple of (clean_content, entity_references, chunk_citations).

    """
    clean = strip_thinking_tags(content)
    clean = normalize_chunk_references(clean, tool_results or None)
    if tool_results:
        clean = correct_mismatched_citations(clean, tool_results)
        clean = inject_citations_into_blockquotes(clean, tool_results)
        # Fallback: when the LLM quotes chunk text in plain prose without a
        # blockquote AND without a [[cite:...]] marker (the common failure
        # mode for smaller local models), append a citation to the
        # paragraph if any inline quoted span matches a chunk.
        clean = inject_citations_for_uncited_paragraphs(clean, tool_results)
        # Strip blockquotes before citations (citation-by-reference: LLM should
        # not reproduce text, but may still do so despite prompt instructions)
        clean = _strip_blockquotes_before_citations(clean)
        # Strip long inline quotes ("30+ chars") before citations
        clean = _strip_inline_quotes_before_citations(clean)
    # Move trailing punctuation before citation markers so the sentence
    # reads naturally and the punctuation isn't orphaned below the blockquote.
    # e.g. "...clear [[cite:id:S3|file]]." -> "...clear. [[cite:id:S3|file]]"
    clean = re.sub(
        r"(?<=\S)\s*(\[\[cite:[^\]]+\]\])\s*([.;,!?])",
        r"\2 \1",
        clean,
    )

    entity_refs = extract_entity_references(clean)
    if tool_results and entity_refs:
        entity_refs = enrich_entity_references_from_tool_results(entity_refs, tool_results)

    if entity_refs:
        logger.debug(
            "chat_stream_entity_references_extracted",
            chat_id=chat_id,
            reference_count=len(entity_refs),
            entity_ids=list(entity_refs.keys()),
        )

    chunk_cites = extract_chunk_citations(clean)
    if tool_results and chunk_cites:
        chunk_cites = enrich_chunk_citations_from_tool_results(chunk_cites, tool_results)

    # Post-enrichment: strip prose that duplicates enriched sentence_text
    if chunk_cites:
        clean = strip_duplicated_citation_text(clean, chunk_cites)

    return clean, entity_refs, chunk_cites


async def _maybe_validate_response(
    settings: Settings,
    tool_results: list[dict[str, Any]],
    clean_content: str,
    chat_id: str,
    chunk_citations: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run post-response validation if enabled and applicable.

    Prefers reference-based validation when chunk citations are available,
    falling back to deterministic text-matching against search chunks
    when citations are not present.

    Args:
        settings: Application settings (checked for enable_response_validation).
        tool_results: Tool result messages from the conversation.
        clean_content: Cleaned response content to validate.
        chat_id: Chat ID for logging.
        chunk_citations: Extracted chunk citations (when available, enables
            reference-based validation instead of text-matching).

    Returns:
        Validation result dict, or None if validation was skipped/disabled.

    """
    if not (settings and settings.chat_context.enable_response_validation and tool_results):
        return None

    # Prefer reference-based validation when citations are available
    if chunk_citations:
        result = await validate_citation_references(
            tool_results=tool_results,
            citations=chunk_citations,
            chat_id=chat_id,
        )
    else:
        result = await validate_response_grounding(
            tool_results=tool_results,
            response_content=clean_content,
            chat_id=chat_id,
        )

    logger.info(
        "chat_response_validation",
        chat_id=chat_id,
        verdict=result.get("verdict"),
        reason=result.get("reason"),
    )
    return result


async def _save_and_emit_done(  # noqa: C901, PLR0912
    content: str,
    thinking: str | None,
    chat_id: str,
    chat_service: Any,
    messages_for_llm: list[Any],
    iterations: int,
    tool_calls_made: int,
    llm_debug: LLMDebugInfo | None = None,
    enrich_from_tools: bool = False,
    settings: Settings | None = None,
    all_tool_calls: list[Any] | None = None,
    all_cached_tool_calls: list[Any] | None = None,
) -> AsyncIterator[bytes]:
    """Save final message, track tokens, and emit the SSE done event.

    Shared finalization logic used by both tool-call and no-tool-call paths.

    Args:
        content: Final response content (already cleaned of thinking tags by caller).
        thinking: Thinking content if any.
        chat_id: Chat ID for logging.
        chat_service: ChatService instance.
        messages_for_llm: Input messages sent to LLM (for token estimation).
        iterations: Number of tool iterations completed (0 for no-tool path).
        tool_calls_made: Number of tool calls made (0 for no-tool path).
        llm_debug: Optional debug info for advanced UI display.
        enrich_from_tools: Whether to enrich entity references from tool results.
        settings: Optional application settings (needed for validation toggle).
        all_tool_calls: All executed tool calls for persistence to database.
        all_cached_tool_calls: Deduplicated tool calls skipped during execution.

    Yields:
        SSE-formatted done event (and thinking event if thinking is present).

    """
    # Extract thinking from <think> tags when no native thinking was provided
    # (providers like DeepSeek emit thinking via <think> tags in content)
    if not thinking:
        thinking = extract_thinking_from_tags(content)

    # Send thinking if available
    if thinking:
        yield format_sse_event("thinking", {"thinking": thinking})

    # Collect tool results once for alias resolution and enrichment
    tool_results: list[dict[str, Any]] = []
    if enrich_from_tools:
        tool_results = [msg for msg in messages_for_llm if msg.get("role") == "tool"]

    # Clean, normalize, and extract references from content
    clean_content, entity_references, chunk_citations = _process_response_content(
        content, tool_results, chat_id
    )

    # Validate response grounding (if enabled and tool results exist)
    validation_result = await _maybe_validate_response(
        settings,
        tool_results,
        clean_content,
        chat_id,
        chunk_citations=chunk_citations,
    )

    # Build extra metadata
    extra_metadata: dict[str, Any] = {}
    if thinking:
        extra_metadata["thinking"] = thinking
    if all_tool_calls:
        extra_metadata["tool_calls"] = all_tool_calls
    if all_cached_tool_calls:
        extra_metadata["cached_tool_calls"] = all_cached_tool_calls
    if entity_references:
        extra_metadata["referenced_entities"] = entity_references
    if chunk_citations:
        extra_metadata["chunk_citations"] = chunk_citations
    if validation_result:
        extra_metadata["validation"] = validation_result

    # Compute final timing metrics
    _compute_final_timing(llm_debug, clean_content)

    # Update and include LLM debug info
    if llm_debug:
        llm_debug.final_messages = copy.deepcopy(messages_for_llm)
        llm_debug.response_content = clean_content
        llm_debug.iterations = iterations
        llm_debug.tool_calls_made = tool_calls_made
        extra_metadata["llm_debug"] = llm_debug.to_dict()

    # Save final message
    chat_service.add_message(
        chat_id, "assistant", clean_content, extra_metadata if extra_metadata else None
    )
    chat_service.update_chat_status(chat_id, "active")

    # Track estimated tokens (and record against the daily spend cap)
    await _track_streaming_tokens(messages_for_llm, content, chat_id, settings=settings)

    # Build and emit done event (include normalized content so frontend can update)
    done_data: dict[str, Any] = {"content": clean_content}
    if iterations:
        done_data["iterations"] = iterations
    if thinking:
        done_data["thinking"] = thinking
    if entity_references:
        done_data["referenced_entities"] = entity_references
    if chunk_citations:
        done_data["chunk_citations"] = chunk_citations
    if llm_debug:
        done_data["llm_debug"] = llm_debug.to_dict()
    if validation_result:
        done_data["validation"] = validation_result
    if all_cached_tool_calls:
        done_data["cached_tool_calls"] = all_cached_tool_calls
    yield format_sse_event("done", done_data)


async def _finalize_tool_response(
    final_content: str,
    final_thinking: str | None,
    chat_id: str,
    chat_service: Any,
    chat_provider: Any,
    messages_for_llm: list[Any],
    iteration: int,
    llm_debug: LLMDebugInfo | None = None,
    settings: Settings | None = None,
    all_cached_tool_calls: list[Any] | None = None,
) -> AsyncIterator[bytes]:
    """Finalize response after tool iteration loop completes.

    Handles empty responses with recovery calls and saves the final message.

    Args:
        final_content: Final accumulated content
        final_thinking: Final thinking content if any
        chat_id: Chat ID for logging
        chat_service: ChatService instance
        chat_provider: LLM provider for recovery calls
        messages_for_llm: Current message history
        iteration: Total iterations completed
        llm_debug: Optional debug info for LLM calls
        settings: Optional application settings (for validation toggle)
        all_cached_tool_calls: Deduplicated tool calls skipped during execution

    Yields:
        SSE-formatted response chunks

    """
    # Handle empty response with recovery
    if not final_content or final_content.strip() == "":
        logger.warning(
            "chat_stream_empty_response_after_tools",
            chat_id=chat_id,
            has_thinking=bool(final_thinking),
            total_iterations=iteration,
        )
        final_content = await _attempt_recovery_call(
            chat_provider=chat_provider,
            messages_for_llm=messages_for_llm,
            chat_id=chat_id,
            settings=settings,
        )
        yield format_sse_event("content", {"delta": final_content, "accumulated": final_content})

    # Count total tool calls and collect tool call objects from assistant messages
    tool_calls_made = sum(1 for msg in messages_for_llm if msg.get("role") == "tool")
    all_tool_calls = [
        tc
        for msg in messages_for_llm
        if msg.get("role") == "assistant" and msg.get("tool_calls")
        for tc in msg["tool_calls"]
    ]

    async for chunk in _save_and_emit_done(
        content=final_content,
        thinking=final_thinking,
        chat_id=chat_id,
        chat_service=chat_service,
        messages_for_llm=messages_for_llm,
        iterations=iteration,
        tool_calls_made=tool_calls_made,
        llm_debug=llm_debug,
        enrich_from_tools=True,
        settings=settings,
        all_tool_calls=all_tool_calls,
        all_cached_tool_calls=all_cached_tool_calls,
    ):
        yield chunk


async def _attempt_recovery_call(
    chat_provider: Any,
    messages_for_llm: list[Any],
    chat_id: str,
    settings: Settings,
) -> str:
    """Attempt a recovery LLM call when response is empty.

    Args:
        chat_provider: LLM provider instance
        messages_for_llm: Current message history
        chat_id: Chat ID for logging
        settings: Application settings for thinking configuration

    Returns:
        Response content (recovery or fallback)

    """
    force_response_messages = [
        *messages_for_llm,
        {
            "role": "user",
            "content": (
                "Based on the tool execution results above, please provide your response to me. "
                "Do not just think - actually respond with what the tool results mean and what was accomplished."
            ),
        },
    ]

    try:
        third_result = await chat_provider.chat(
            messages=force_response_messages,
            tools=None,
            stream=True,
            enable_thinking=settings.llm.thinking_for_chat,
        )

        if hasattr(third_result, "__aiter__"):
            accumulated_third = ""

            try:
                async for third_chunk in third_result:
                    third_type = third_chunk.get("type")

                    if third_type == "content":
                        accumulated_third = third_chunk.get("accumulated", accumulated_third)
                    elif third_type == "done":
                        accumulated_third = third_chunk.get("content", accumulated_third)
            finally:
                # Ensure stream is closed even if exception occurs (prevents semaphore leak)
                await _close_stream(third_result)

            if accumulated_third and accumulated_third.strip():
                logger.info(
                    "chat_stream_recovery_call_succeeded",
                    chat_id=chat_id,
                    content_length=len(accumulated_third),
                )
                return accumulated_third

        logger.warning("chat_stream_recovery_call_empty", chat_id=chat_id)

    except Exception as e:
        logger.exception(
            "chat_stream_recovery_call_failed",
            chat_id=chat_id,
            error_type=type(e).__name__,
            error_message=str(e),
        )

    return create_fallback_response(has_thinking=False, after_tools=True)


async def _track_streaming_tokens(
    messages_for_llm: list,
    accumulated_content: str,
    chat_id: str,
    settings: Settings | None = None,
) -> None:
    """Track estimated token usage for streaming chat.

    Since streaming doesn't provide exact token counts, we estimate using
    a simple heuristic (4 chars ~ 1 token).

    Args:
        messages_for_llm: Input messages sent to LLM
        accumulated_content: Output content from LLM
        chat_id: Chat ID for logging
        settings: Application settings. When provided, this turn's estimated
            tokens are also recorded against the persisted daily spend cap so a
            configured ``max_tokens_per_day`` actually bounds streaming chat.

    """
    try:
        input_tokens = estimate_message_tokens(messages_for_llm)
        output_tokens = estimate_tokens(accumulated_content)

        await queue_client.track_tokens(QUEUE_LLM, input_tokens, output_tokens)

        # Feed the persisted daily spend cap (the queued chat path records via
        # LLMQueueService; the streaming path must do the same or the cap is
        # blind to interactive chat).
        if settings is not None:
            await asyncio.to_thread(_spend_record, settings, input_tokens + output_tokens)

        logger.debug(
            "chat_stream_tokens_tracked",
            chat_id=chat_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except Exception as e:
        # Don't fail the chat if token tracking fails
        logger.warning(
            "chat_stream_token_tracking_failed",
            chat_id=chat_id,
            error_type=type(e).__name__,
            error_message=str(e),
        )


async def _finalize_response(
    accumulated_content: str,
    thinking: str | None,
    chat_id: str,
    chat_service: Any,
    messages_for_llm: list[Any],
    llm_debug: LLMDebugInfo | None = None,
    tools_were_available: bool = True,
    settings: Settings | None = None,
) -> AsyncIterator[bytes]:
    """Finalize response when no tool calls were made.

    Args:
        accumulated_content: Accumulated content from LLM
        thinking: Thinking content if any
        chat_id: Chat ID for logging
        chat_service: ChatService instance
        messages_for_llm: Input messages sent to LLM (for token estimation)
        llm_debug: Optional debug info for advanced UI display
        tools_were_available: Whether tools were passed to LLM (helps diagnose issues)
        settings: Optional application settings (for validation toggle).

    Yields:
        SSE-formatted response chunks

    """
    # Handle empty response
    if not accumulated_content or accumulated_content.strip() == "":
        logger.warning(
            "chat_stream_empty_response_no_tools",
            chat_id=chat_id,
            has_thinking=bool(thinking),
            tools_were_available=tools_were_available,
        )
        accumulated_content = create_fallback_response(
            has_thinking=bool(thinking),
            tools_were_available=tools_were_available,
        )
        yield format_sse_event(
            "content", {"delta": accumulated_content, "accumulated": accumulated_content}
        )

    async for chunk in _save_and_emit_done(
        content=accumulated_content,
        thinking=thinking,
        chat_id=chat_id,
        chat_service=chat_service,
        messages_for_llm=messages_for_llm,
        iterations=0,
        tool_calls_made=0,
        llm_debug=llm_debug,
        settings=settings,
    ):
        yield chunk


__all__ = [
    "LLMDebugInfo",
    "_save_done_chunk_timing",
    "stream_chat_response",
]
