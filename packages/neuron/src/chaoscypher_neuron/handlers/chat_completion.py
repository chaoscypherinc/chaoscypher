# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat completion handler (LLM queue).

Registers a handler on the LLM queue that runs the full chat LLM + tool
chain in the background.  Messages are persisted to the database and
streaming events are published to a per-chat Valkey pub/sub channel so
the SSE endpoint can relay them to the client.
"""

import asyncio
import copy
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chaoscypher_core.constants import QUEUE_LLM
from chaoscypher_core.queue import queue_client
from chaoscypher_core.queue.pubsub import publish_chat_event
from chaoscypher_core.utils.logging.app_config import get_logger


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.app_config.manager import ConfigManager
    from chaoscypher_core.services.chat.management.service import ChatService

logger = get_logger(__name__)

__all__ = ["register_chat_completion_handler"]


@dataclass
class _ToolLoopState:
    """Mutable state for the tool-calling iteration loop.

    Tracks accumulated content, thinking, and tool call counts across
    multiple LLM -> tool -> LLM iterations.
    """

    content: str = ""
    thinking: str | None = None
    all_thinking_parts: list[str] = field(default_factory=list)
    total_tool_calls: int = 0
    iteration: int = 0


def register_chat_completion_handler(
    storage_adapter: SqliteAdapter,
    settings: Settings,
    config_manager: ConfigManager,
    graph_repository: GraphRepository,
    search_repository: SearchRepository,
    current_database: str,
) -> None:
    """Register the chat completion handler on the LLM queue.

    Args:
        storage_adapter: SqliteAdapter implementing ChatStorageProtocol,
            SourceStorageProtocol, and IndexingProtocol.
        settings: Application settings.
        config_manager: Configuration manager.
        graph_repository: Graph repository for tool execution.
        search_repository: Search repository for tool execution.
        current_database: Default database name.

    """

    async def chat_completion_handler(
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a full chat LLM + tool chain in the background.

        Loads the chat, builds the LLM context, streams the response,
        executes tools if requested, and persists the final assistant
        message.  Events are published via Valkey pub/sub for the SSE
        endpoint.

        Args:
            data: Task data containing ``chat_id`` and optionally
                ``database_name``.
            metadata: Task metadata (unused).
            task_id: Task ID from queue.

        Returns:
            Result dictionary with success flag and response summary.

        """
        from chaoscypher_core.services.chat.management.service import ChatService
        from chaoscypher_neuron.handlers import validate_database_name

        chat_id = data.get("chat_id")
        if not chat_id:
            msg = "chat_id is required in task data"
            raise ValueError(msg)
        db_name = validate_database_name(data.get("database_name"), current_database)

        chat_service = ChatService(storage=storage_adapter, database_name=db_name)

        logger.info(
            "chat_completion_started",
            chat_id=chat_id,
            database_name=db_name,
            task_id=task_id,
        )

        # Ensure chat is in "processing" state.  On the first attempt this
        # is already set by the POST /send endpoint, but on a retry after a
        # transient failure the error handler below will have set it to
        # "error" — reset it so the user sees processing status again.
        chat_service.update_chat_status(chat_id, "processing")

        try:
            return await _run_chat_completion(
                chat_id=chat_id,
                chat_service=chat_service,
                storage_adapter=storage_adapter,
                settings=settings,
                config_manager=config_manager,
                graph_repository=graph_repository,
                search_repository=search_repository,
            )

        except Exception:
            logger.exception("chat_completion_failed", chat_id=chat_id)

            # Always update chat state so the user sees the error in the UI
            chat_service.update_chat_status(chat_id, "error")
            await publish_chat_event(
                chat_id,
                "error",
                {
                    "error": "An unexpected error occurred during chat completion",
                    "error_code": "WORKER_ERROR",
                },
            )

            # Re-raise so _execute_handler can classify the error and
            # retry transient failures (LLM timeouts, network errors).
            # Permanent errors will be caught by the framework and marked
            # failed without retry.
            raise

    queue_client.register_handlers(QUEUE_LLM, {"chat_background": chat_completion_handler})


def _spend_check(settings: Settings) -> None:
    """Enforce the daily LLM spend cap before a background chat turn.

    Raises :class:`LLMSpendCapExceededError` (a permanent, non-retryable
    ``LLMError``) when a configured per-day cap is reached, so the outer handler
    marks the chat failed without retry. Opens and closes a short-lived adapter
    on the active database's ``app.db`` — the same pattern the LLM-queue worker
    uses in ``LLMQueueService._chat_handler_wrapper`` (chat runs at QUEUE_LLM
    concurrency 1, so this is one short-lived session per call; ``disconnect``
    closes only the session, not the shared cached engine). The per-source cap
    is skipped (chat is interactive, not tied to one source) — only the daily
    cap applies.
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
    """Add a completed background chat turn's tokens to the persisted daily total.

    No-op for non-positive ``total_tokens``. Best-effort: a storage failure is
    swallowed by the tracker and never breaks the just-completed chat.
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


async def _record_turn_spend(
    settings: Settings,
    messages_for_llm: list[dict[str, Any]],
    content: str,
) -> None:
    """Record this turn's estimated tokens against the persisted daily spend cap.

    Streaming responses carry no exact usage, so tokens are estimated (~4 chars
    per token) exactly as the interactive ``_track_streaming_tokens`` path does.
    Best-effort: a tracking failure is logged and never breaks the
    just-completed chat. Offloaded to a thread — the record opens an adapter and
    runs blocking SQLite that must not stall the event loop.
    """
    try:
        from chaoscypher_core.utils.tokens import estimate_message_tokens, estimate_tokens

        input_tokens = estimate_message_tokens(messages_for_llm)
        output_tokens = estimate_tokens(content)
        await asyncio.to_thread(_spend_record, settings, input_tokens + output_tokens)
    except Exception:
        logger.warning("chat_completion_spend_record_failed", exc_info=True)


async def _run_chat_completion(
    chat_id: str,
    chat_service: ChatService,
    storage_adapter: SqliteAdapter,
    settings: Settings,
    config_manager: ConfigManager,
    graph_repository: GraphRepository,
    search_repository: SearchRepository,
) -> dict[str, Any]:
    """Execute the full chat completion pipeline.

    Loads the chat, resolves source scope, builds LLM messages, makes the
    initial LLM call, handles tool loops if needed, saves the final
    assistant message, and publishes the done event.

    Args:
        chat_id: Chat session identifier.
        chat_service: ChatService instance for DB operations.
        storage_adapter: Storage adapter for source lookups.
        settings: Application settings.
        config_manager: Configuration manager.
        graph_repository: Graph repository for tool execution.
        search_repository: Search repository for tool execution.

    Returns:
        Result dictionary with success flag and response summary.

    Raises:
        ValueError: If the chat is not found.

    """
    from chaoscypher_core.services.workflows.tools import get_tool_discovery
    from chaoscypher_core.streaming.chat import (
        build_messages_for_llm,
        get_model_name,
        setup_chat_providers,
    )

    # Load chat from database
    chat = chat_service.get_chat(chat_id)
    if not chat:
        await publish_chat_event(
            chat_id, "error", {"error": "Chat not found", "error_code": "CHAT_NOT_FOUND"}
        )
        msg = f"Chat {chat_id} not found"
        raise ValueError(msg)

    # Buffer every message this run produces (tool results + the final
    # assistant answer) and persist them only once the run succeeds. A
    # transient error mid-run re-raises so the queue worker retries the same
    # task_id and re-runs this function from the top; deferring all writes to
    # _finalize_and_publish means a failed attempt leaves nothing behind, so
    # the retry cannot duplicate tool/assistant rows or pollute the rebuilt
    # LLM context. See test_chat_completion_idempotency.
    pending_messages: list[dict[str, Any]] = []

    # Status already set to "processing" by the POST /send endpoint

    # Resolve source scope for scoped chats
    source_ids = chat.get("source_ids")
    source_metadata = None
    if source_ids:
        source_metadata = []
        for sid in source_ids:
            source = storage_adapter.get_source(sid, settings.current_database)
            if source:
                source_metadata.append(
                    {
                        "id": sid,
                        "title": source.get("title", source.get("filename", sid)),
                    }
                )

    # Setup providers and tools.
    # The worker runs ON the LLM queue (concurrency=1), so tool LLM callbacks
    # must call the provider directly — not enqueue on the same queue (deadlock).
    from chaoscypher_core.llm_queue.factory import get_provider_factory

    _factory = get_provider_factory()
    _direct_provider = _factory.get_chat_provider()

    async def _direct_llm_callback(
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Direct LLM call bypassing the queue (worker-safe)."""
        return await _direct_provider.chat(
            messages=messages,
            stream=False,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    chat_provider, tool_executor, available_tools = setup_chat_providers(
        settings,
        graph_repository,
        search_repository,
        chat_id,
        indexing_manager=storage_adapter,
        source_ids=source_ids,
        source_storage=storage_adapter,
        llm_chat_callback_override=_direct_llm_callback,
    )

    # Warm the tool discovery singleton (ensures plugins are discovered)
    get_tool_discovery()

    # Build messages for LLM
    build_result = build_messages_for_llm(chat, chat_id, settings, source_metadata=source_metadata)
    messages_for_llm = build_result.messages_for_llm
    context_info = build_result.context_info

    model_name = get_model_name(settings)
    logger.info(
        "chat_completion_llm_config",
        chat_id=chat_id,
        provider=settings.llm.chat_provider.lower(),
        model=model_name,
        message_count=len(messages_for_llm),
        tool_count=len(available_tools) if available_tools else 0,
    )

    # Publish context info for the SSE subscriber
    await publish_chat_event(
        chat_id,
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

    # Build LLM debug info for advanced UI display
    from chaoscypher_core.streaming.chat import LLMDebugInfo

    provider_name = settings.llm.chat_provider.lower()
    llm_debug = LLMDebugInfo(
        provider=provider_name,
        model=model_name,
        initial_messages=copy.deepcopy(messages_for_llm),
        tools=available_tools or [],
    )

    # First LLM call
    enable_thinking = settings.llm.thinking_for_chat

    # Enforce the daily spend cap before spending tokens on this turn. The
    # background chat path calls the provider directly (bypassing
    # LLMQueueService._chat_handler_wrapper), so without this gate a configured
    # max_tokens_per_day is blind to the primary chat surface — mirror
    # stream_chat_response. LLMSpendCapExceededError is permanent, so the outer
    # handler marks the chat failed without retry. Offloaded to a thread: the
    # check opens an adapter + runs blocking SQLite that must not stall the loop.
    await asyncio.to_thread(_spend_check, settings)

    stream_start = time.monotonic()
    llm_result = await chat_provider.chat(
        messages=messages_for_llm,
        tools=available_tools,
        stream=True,
        enable_thinking=enable_thinking,
    )
    logger.info("chat_completion_llm_call_initiated", chat_id=chat_id)

    # Consume the streaming response
    content, thinking, tool_calls, stream_error = await _consume_llm_stream(llm_result, chat_id)

    # If the initial LLM call errored, set chat to error and bail out
    if stream_error:
        chat_service.update_chat_status(chat_id, "error")
        return {"success": False, "chat_id": chat_id, "error": "LLM streaming failed"}

    # Handle tool loop if the LLM requested tools
    total_tool_calls = 0
    tool_loop_error = False
    if tool_calls:
        content, thinking, total_tool_calls, tool_loop_error = await _handle_tool_loop(
            tool_calls=tool_calls,
            content=content,
            thinking=thinking,
            chat_id=chat_id,
            chat_service=chat_service,
            chat_provider=chat_provider,
            tool_executor=tool_executor,
            available_tools=available_tools,
            messages_for_llm=messages_for_llm,
            settings=settings,
            pending_messages=pending_messages,
        )

    # If tool loop errored, set chat to error and bail out
    if tool_loop_error:
        chat_service.update_chat_status(chat_id, "error")
        return {"success": False, "chat_id": chat_id, "error": "LLM failed during tool processing"}

    result = await _finalize_and_publish(
        content=content,
        thinking=thinking,
        chat_id=chat_id,
        chat_service=chat_service,
        messages_for_llm=messages_for_llm,
        llm_debug=llm_debug,
        stream_start=stream_start,
        total_tool_calls=total_tool_calls,
        pending_messages=pending_messages,
    )

    # Record this turn's tokens against the daily spend cap (success path only —
    # the stream/tool-loop error bail-outs above return before here, the same as
    # stream_chat_response, which records only on its success path).
    await _record_turn_spend(settings, messages_for_llm, content)

    return result


async def _finalize_and_publish(
    content: str,
    thinking: str | None,
    chat_id: str,
    chat_service: Any,
    messages_for_llm: list[Any],
    llm_debug: Any,
    stream_start: float,
    total_tool_calls: int,
    pending_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Clean content, persist the buffered messages, and publish the done event.

    This is the run's only persistence point: the assistant message is
    appended to ``pending_messages`` (which already holds any tool-result
    messages produced during the loop) and the whole buffer is flushed here,
    on the success path. A run that raised earlier never reaches this call, so
    its buffered messages are simply discarded — that is what makes a
    transient-error retry idempotent.

    Args:
        content: Raw accumulated content (may contain <think> tags).
        thinking: Thinking content from native thinking providers.
        chat_id: Chat session identifier.
        chat_service: ChatService for DB persistence.
        messages_for_llm: Full message history for debug info.
        llm_debug: LLMDebugInfo accumulator.
        stream_start: Monotonic timestamp of stream start for timing.
        total_tool_calls: Number of tool calls executed.
        pending_messages: Buffered, not-yet-persisted messages from this run
            (tool results); the assistant message is appended and the buffer
            is flushed before the done event is published.

    Returns:
        Result dictionary with success flag and response summary.

    """
    from chaoscypher_core.streaming.chat import (
        correct_mismatched_citations,
        enrich_chunk_citations_from_tool_results,
        enrich_entity_references_from_tool_results,
        extract_chunk_citations,
        extract_entity_references,
        inject_citations_for_uncited_paragraphs,
        inject_citations_into_blockquotes,
        normalize_chunk_references,
        strip_duplicated_citation_text,
        strip_thinking_tags,
    )
    from chaoscypher_core.streaming.chat.utils import extract_thinking_from_tags

    clean_content = strip_thinking_tags(content) if content else ""
    if not clean_content.strip():
        clean_content = "I apologize, but I was unable to generate a response. Please try again."

    # Extract thinking from <think> tags if not already provided
    if not thinking:
        thinking = extract_thinking_from_tags(content) if content else None

    # ---- Citation post-processing ------------------------------------------
    # Collect tool result messages so the citation injectors can match
    # quoted prose back to the chunks the LLM actually saw. Tool messages in
    # ``messages_for_llm`` carry the original chunk JSON the LLM consumed.
    tool_results = [m for m in messages_for_llm if m.get("role") == "tool"]

    if tool_results and clean_content:
        clean_content = normalize_chunk_references(clean_content, tool_results)
        clean_content = correct_mismatched_citations(clean_content, tool_results)
        clean_content = inject_citations_into_blockquotes(clean_content, tool_results)
        # Fallback: when the LLM forgot the [[cite:...]] marker but quoted
        # chunk text inline, append a marker so the UI can render the
        # supporting blockquote / pill instead of leaving the claim
        # unsourced. (Bug #4 v2.)
        clean_content = inject_citations_for_uncited_paragraphs(clean_content, tool_results)
    elif clean_content:
        clean_content = normalize_chunk_references(clean_content, None)

    # Extract structured references for the done event so the frontend can
    # hydrate inline citation chips and entity links.
    chunk_citations = extract_chunk_citations(clean_content)
    if tool_results and chunk_citations:
        chunk_citations = enrich_chunk_citations_from_tool_results(chunk_citations, tool_results)
        # Once enriched sentence_text is present, drop any prose that
        # duplicates the cited quote (the UI already renders it once via
        # the citation blockquote).
        clean_content = strip_duplicated_citation_text(clean_content, chunk_citations)

    entity_refs = extract_entity_references(clean_content)
    if tool_results and entity_refs:
        entity_refs = enrich_entity_references_from_tool_results(entity_refs, tool_results)
    # ------------------------------------------------------------------------

    # Collect all tool call objects from the message history
    all_tool_calls = [
        tc
        for msg in messages_for_llm
        if msg.get("role") == "assistant" and msg.get("tool_calls")
        for tc in msg["tool_calls"]
    ]

    # Finalize LLM debug info with timing
    total_ms = round((time.monotonic() - stream_start) * 1000)
    llm_debug.timing["total_ms"] = total_ms
    llm_debug.final_messages = copy.deepcopy(messages_for_llm)
    llm_debug.response_content = clean_content
    llm_debug.iterations = total_tool_calls
    llm_debug.tool_calls_made = total_tool_calls

    # Build extra metadata
    extra_metadata: dict[str, Any] = {}
    if thinking:
        extra_metadata["thinking"] = thinking
    if all_tool_calls:
        extra_metadata["tool_calls"] = all_tool_calls
    extra_metadata["llm_debug"] = llm_debug.to_dict()
    # Persist enriched citation / entity-reference metadata so the
    # source-text blockquote and entity pills survive a page refresh.
    # Without this, the chat history endpoint returns only the raw
    # ``[[cite:...]]`` markers and the UI loses the supporting sentences.
    if chunk_citations:
        extra_metadata["chunk_citations"] = dict(chunk_citations)
    if entity_refs:
        extra_metadata["entity_references"] = dict(entity_refs)

    pending_messages.append(
        chat_service.build_message(chat_id, "assistant", clean_content, extra_metadata)
    )
    # Single persistence point for the whole run — tool results buffered during
    # the loop plus this assistant answer, written together on success.
    chat_service.persist_messages(pending_messages)
    chat_service.update_chat_status(chat_id, "active")

    # Publish done event
    done_data: dict[str, Any] = {
        "status": "completed",
        "content": clean_content,
        "llm_debug": llm_debug.to_dict(),
    }
    if thinking:
        done_data["thinking"] = thinking
    if all_tool_calls:
        done_data["tool_calls"] = all_tool_calls
    # ChunkCitationData / EntityRefData are TypedDicts — already JSON-serialisable.
    if chunk_citations:
        done_data["chunk_citations"] = dict(chunk_citations)
    if entity_refs:
        done_data["entity_references"] = dict(entity_refs)
    await publish_chat_event(chat_id, "done", done_data)

    logger.info(
        "chat_completion_done",
        chat_id=chat_id,
        content_length=len(clean_content),
        tool_calls_made=total_tool_calls,
    )

    return {
        "success": True,
        "chat_id": chat_id,
        "content_length": len(clean_content),
        "tool_calls_made": total_tool_calls,
    }


async def _consume_llm_stream(
    llm_result: Any,
    chat_id: str,
) -> tuple[str, str | None, list[Any] | None, bool]:
    """Consume an LLM streaming response and publish events via pub/sub.

    Iterates over the async stream, forwarding content and thinking
    deltas to the pub/sub channel, and returns the accumulated state.

    Args:
        llm_result: Async iterator of LLM response chunks.
        chat_id: Chat ID for event publishing.

    Returns:
        Tuple of (accumulated_content, thinking, tool_calls, stream_error).

    """
    accumulated_content = ""
    thinking: str | None = None
    tool_calls: list[Any] | None = None
    stream_error = False

    try:
        async for chunk in llm_result:
            chunk_type = chunk.get("type")

            if chunk_type == "content":
                delta = chunk.get("delta", "")
                accumulated_content = chunk.get("accumulated", accumulated_content)
                await publish_chat_event(
                    chat_id,
                    "content",
                    {"delta": delta, "accumulated": accumulated_content},
                )

            elif chunk_type == "thinking_delta":
                thinking = chunk.get("accumulated", "")
                await publish_chat_event(chat_id, "thinking_delta", {"thinking": thinking})

            elif chunk_type == "error":
                error_msg = chunk.get("error", "Unknown LLM error")
                error_code = chunk.get("error_code", "LLM_ERROR")
                logger.error(
                    "chat_completion_stream_error",
                    chat_id=chat_id,
                    error=error_msg,
                    error_code=error_code,
                )
                await publish_chat_event(
                    chat_id,
                    "error",
                    {"error": error_msg, "error_code": error_code},
                )
                stream_error = True
                break

            elif chunk_type == "done":
                accumulated_content = chunk.get("content", accumulated_content)
                thinking = chunk.get("thinking", thinking)
                tool_calls = chunk.get("tool_calls")
                logger.info(
                    "chat_completion_stream_done",
                    chat_id=chat_id,
                    content_length=len(accumulated_content) if accumulated_content else 0,
                    tool_call_count=len(tool_calls) if tool_calls else 0,
                )
                break
    finally:
        if hasattr(llm_result, "aclose"):
            await llm_result.aclose()

    return accumulated_content, thinking, tool_calls, stream_error


async def _handle_tool_loop(
    tool_calls: list[Any],
    content: str,
    thinking: str | None,
    chat_id: str,
    chat_service: Any,
    chat_provider: Any,
    tool_executor: Any,
    available_tools: list[Any],
    messages_for_llm: list[Any],
    settings: Any,
    pending_messages: list[dict[str, Any]],
) -> tuple[str, str | None, int, bool]:
    """Execute the iterative tool-calling loop.

    Runs up to ``MAX_TOOL_ITERATIONS`` rounds of tool execution followed
    by follow-up LLM calls.  Each round publishes tool events, executes
    tools, and checks whether the LLM wants to call more tools.

    Args:
        tool_calls: Initial tool calls from the first LLM response.
        content: Accumulated content from the first LLM response.
        thinking: Thinking content from the first LLM response.
        chat_id: Chat session identifier.
        chat_service: ChatService instance for DB persistence.
        chat_provider: LLM provider for follow-up calls.
        tool_executor: ToolExecutorService for executing tools.
        available_tools: Available tool schemas.
        messages_for_llm: Mutable message history.
        settings: Application settings.
        pending_messages: Run-level buffer; tool-result messages are appended
            here (not persisted immediately) and flushed on success by
            _finalize_and_publish.

    Returns:
        Tuple of (final_content, final_thinking, total_tool_calls, error_occurred).

    """
    from chaoscypher_core.services.chat.engine.constants import (
        MAX_TOOL_ITERATIONS,
        MAX_TOTAL_TOOL_CALLS,
    )
    from chaoscypher_core.streaming.chat import strip_thinking_tags

    state = _ToolLoopState(
        content=content,
        thinking=thinking,
        all_thinking_parts=[thinking] if thinking else [],
    )
    current_tool_calls: list[Any] | None = tool_calls
    error_occurred = False

    while current_tool_calls and state.iteration < MAX_TOOL_ITERATIONS:
        state.iteration += 1
        batch_size = len(current_tool_calls)
        state.total_tool_calls += batch_size

        logger.info(
            "chat_completion_tool_iteration",
            chat_id=chat_id,
            iteration=state.iteration,
            tool_count=batch_size,
            total_tool_calls=state.total_tool_calls,
        )

        # Publish tool_calls event
        await publish_chat_event(
            chat_id,
            "tool_calls",
            {"tool_calls": current_tool_calls, "iteration": state.iteration},
        )

        # Check total tool call limit
        if state.total_tool_calls > MAX_TOTAL_TOOL_CALLS:
            logger.warning(
                "chat_completion_tool_limit_reached",
                chat_id=chat_id,
                total=state.total_tool_calls,
                limit=MAX_TOTAL_TOOL_CALLS,
            )
            await publish_chat_event(
                chat_id,
                "warning",
                {"message": "Tool call limit reached", "limit": MAX_TOTAL_TOOL_CALLS},
            )
            break

        # Add assistant message with tool calls to LLM context
        clean_content = strip_thinking_tags(state.content)
        messages_for_llm.append(
            {
                "role": "assistant",
                "content": clean_content,
                "tool_calls": current_tool_calls,
            }
        )

        # Execute each tool in the batch
        for tool_call in current_tool_calls:
            await _execute_tool(
                tool_call=tool_call,
                chat_id=chat_id,
                chat_service=chat_service,
                tool_executor=tool_executor,
                messages_for_llm=messages_for_llm,
                iteration=state.iteration,
                pending_messages=pending_messages,
            )

        # Follow-up LLM call to check for more tools or final response
        followup_result = await chat_provider.chat(
            messages=messages_for_llm,
            tools=available_tools,
            stream=True,
            enable_thinking=settings.llm.thinking_for_tools,
        )

        (
            followup_content,
            followup_thinking,
            next_tool_calls,
            followup_error,
        ) = await _consume_llm_stream(followup_result, chat_id)

        # If follow-up errored, stop the tool loop (error event already published)
        if followup_error:
            error_occurred = True
            break

        # Update state with follow-up results
        if followup_content:
            state.content = followup_content
        if followup_thinking:
            state.all_thinking_parts.append(followup_thinking)

        current_tool_calls = next_tool_calls

    # Join all thinking parts for multi-step display
    final_thinking = (
        "\n\n---\n\n".join(state.all_thinking_parts) if state.all_thinking_parts else None
    )

    return state.content, final_thinking, state.total_tool_calls, error_occurred


async def _execute_tool(
    tool_call: dict[str, Any],
    chat_id: str,
    chat_service: Any,
    tool_executor: Any,
    messages_for_llm: list[Any],
    iteration: int,
    pending_messages: list[dict[str, Any]],
) -> None:
    """Execute a single tool call, publish events, and buffer the result.

    Parses arguments from the tool call, runs the tool via the executor,
    publishes start/result events, buffers the tool result message for
    end-of-run persistence, and appends it to the LLM message history.

    Args:
        tool_call: Tool call dict with ``function.name`` and
            ``function.arguments``.
        chat_id: Chat session identifier.
        chat_service: ChatService used to build (not yet persist) the result.
        tool_executor: ToolExecutorService for execution.
        messages_for_llm: Mutable message history.
        iteration: Current tool iteration number.
        pending_messages: Run-level buffer the tool result is appended to;
            flushed on success by _finalize_and_publish so a transient-error
            retry never duplicates it.

    """
    from chaoscypher_core.streaming.chat import parse_tool_arguments

    function = tool_call.get("function", {})
    tool_name = function.get("name", "unknown")
    arguments_raw = function.get("arguments", {})
    arguments = parse_tool_arguments(arguments_raw, tool_name, chat_id)
    tool_call_id = tool_call.get("id")

    logger.info(
        "chat_completion_tool_executing",
        chat_id=chat_id,
        tool_name=tool_name,
        iteration=iteration,
    )

    # Publish tool_start event
    await publish_chat_event(
        chat_id,
        "tool_start",
        {"tool": tool_name, "arguments": arguments, "iteration": iteration},
    )

    # Execute tool with timing
    tool_start = time.monotonic()
    result = await tool_executor.execute_tool(tool_name, arguments)
    duration_ms = round((time.monotonic() - tool_start) * 1000)

    result_json = json.dumps(result)
    logger.info(
        "chat_completion_tool_result",
        chat_id=chat_id,
        tool_name=tool_name,
        result_preview=result_json[:200],
        iteration=iteration,
        duration_ms=duration_ms,
    )

    # Publish tool_result event
    await publish_chat_event(
        chat_id,
        "tool_result",
        {
            "tool": tool_name,
            "result": result,
            "iteration": iteration,
            "tool_call_id": tool_call_id,
            "duration_ms": duration_ms,
        },
    )

    # Buffer the tool result (persisted at end-of-run, not now) so a
    # transient failure later in the loop leaves nothing to duplicate on retry.
    pending_messages.append(
        chat_service.build_message(
            chat_id,
            "tool",
            result_json,
            {"tool_call_id": tool_call_id, "name": tool_name},
        )
    )

    # Append tool result to LLM message history
    messages_for_llm.append(
        {
            "role": "tool",
            "content": result_json,
            "tool_call_id": tool_call_id,
            "name": tool_name,
        }
    )
