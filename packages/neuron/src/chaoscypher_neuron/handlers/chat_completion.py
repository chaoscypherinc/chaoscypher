# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat completion handler (LLM queue).

Registers a handler on the LLM queue that runs the full chat LLM + tool
chain in the background via the shared :mod:`chaoscypher_core.streaming.chat.loop`.
This module owns the queue/worker concerns: status transitions, idempotent
persistence of the loop's buffered messages, the done-event publication,
and daily spend-cap accounting. The loop itself (iterations, tool
execution, budget compaction, truncation warnings, recovery) lives in core
and is shared with every other chat surface.
"""

import asyncio
import copy
import functools
import time
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

        Loads the chat, builds the LLM context, runs the shared tool loop,
        and persists the final assistant message. Events are published via
        Valkey pub/sub for the SSE endpoint.

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

            # Always update chat state so the user sees the error in the UI.
            # These side effects are best-effort: if either raises (e.g. a DB
            # lock on update_chat_status under concurrency), it must NOT
            # replace the active exception, or the bare `raise` below would
            # re-raise the side-effect error and _execute_handler would
            # classify THAT instead of the real failure — turning a retryable
            # LLM error into a permanent one. Swallow-and-log so the original
            # exception always propagates.
            try:
                chat_service.update_chat_status(chat_id, "error")
                await publish_chat_event(
                    chat_id,
                    "error",
                    {
                        "error": "An unexpected error occurred during chat completion",
                        "error_code": "WORKER_ERROR",
                        # A failed run persisted nothing (buffered-flush
                        # idempotency), so re-running the turn is always safe.
                        "error_details": {
                            "is_retryable": True,
                            "suggested_action": "Click Retry to run this turn again.",
                        },
                    },
                )
            except Exception:
                logger.exception("chat_completion_error_side_effect_failed", chat_id=chat_id)

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

    Loads the chat, resolves source scope, builds LLM messages, runs the
    shared chat tool loop, saves the final assistant message, and publishes
    the done event.

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
    from chaoscypher_core.streaming.chat.approval_broker import ValkeyApprovalBroker
    from chaoscypher_core.streaming.chat.cancellation import clear_cancel, is_cancel_requested
    from chaoscypher_core.streaming.chat.loop import ChatLoopDeps, run_chat_tool_loop
    from chaoscypher_core.streaming.chat.sinks import ValkeyPubSubSink

    # Load chat from database
    chat = chat_service.get_chat(chat_id)
    if not chat:
        await publish_chat_event(
            chat_id, "error", {"error": "Chat not found", "error_code": "CHAT_NOT_FOUND"}
        )
        msg = f"Chat {chat_id} not found"
        raise ValueError(msg)

    # A stale cancel flag from a previous turn must never kill this one.
    await clear_cancel(chat_id)

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

    async def _spend_guard() -> None:
        """Enforce the daily spend cap before spending tokens on this turn.

        LLMSpendCapExceededError is permanent, so the outer handler marks
        the chat failed without retry. Offloaded to a thread: the check
        opens an adapter + runs blocking SQLite that must not stall the loop.
        """
        await asyncio.to_thread(_spend_check, settings)

    deps = ChatLoopDeps(
        chat_id=chat_id,
        provider=chat_provider,
        tool_executor=tool_executor,
        chat_service=chat_service,
        settings=settings,
        sink=ValkeyPubSubSink(chat_id, publish_chat_event),
        # Cross-process approval: POST /chats/{id}/tool_decision (cortex)
        # flips the Valkey key this broker polls.
        approval=ValkeyApprovalBroker(),
        spend_guard=_spend_guard,
        # Cross-process stop: POST /chats/{id}/cancel (cortex) sets the
        # Valkey flag this check polls at step boundaries.
        cancel_check=functools.partial(is_cancel_requested, chat_id),
        tools=available_tools,
    )

    stream_start = time.monotonic()
    loop_result = await run_chat_tool_loop(messages_for_llm, deps)

    if loop_result.error_occurred:
        chat_service.update_chat_status(chat_id, "error")
        error_msg = (
            "LLM streaming failed"
            if loop_result.error_stage == "initial_stream"
            else "LLM failed during tool processing"
        )
        return {"success": False, "chat_id": chat_id, "error": error_msg}

    # Per-tool durations feed the Telemetry HUD (llm_debug.timing.tool_calls)
    llm_debug.timing["tool_calls"] = loop_result.tool_timings

    result = await _finalize_and_publish(
        content=loop_result.content,
        thinking=loop_result.thinking,
        chat_id=chat_id,
        chat_service=chat_service,
        messages_for_llm=messages_for_llm,
        llm_debug=llm_debug,
        stream_start=stream_start,
        total_tool_calls=loop_result.total_tool_calls,
        pending_messages=loop_result.pending_messages,
        warnings=loop_result.warnings,
        settings=settings,
        done_status="cancelled" if loop_result.cancelled else "completed",
    )

    # Record this turn's tokens against the daily spend cap (success path only —
    # the error bail-out above returns before here).
    await _record_turn_spend(settings, messages_for_llm, loop_result.content)

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
    warnings: list[dict[str, str]] | None = None,
    settings: Any = None,
    done_status: str = "completed",
) -> dict[str, Any]:
    """Finalize the answer, persist the buffered messages, publish done.

    Content cleanup and reference extraction live in the shared
    ``finalize_chat_content``; this function owns the worker-side concerns.
    It is the run's only persistence point: the assistant message is
    appended to ``pending_messages`` (which already holds any tool-result
    messages produced during the loop) and the whole buffer is flushed here,
    on the success path. A run that raised earlier never reaches this call,
    so its buffered messages are simply discarded — that is what makes a
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
        warnings: Truncation warnings collected across the turn (persisted so
            the "this answer was cut off" context survives a reload).
        settings: Application settings; enables post-response validation
            when provided (None skips it — unit-test default).
        done_status: Status carried by the ``done`` event — ``"completed"``
            normally, ``"cancelled"`` when the user stopped the turn (the
            partial answer is persisted either way).

    Returns:
        Result dictionary with success flag and response summary.

    """
    from chaoscypher_core.streaming.chat.finalize import (
        build_optional_payload,
        finalize_chat_content,
        validate_finalized_answer,
    )

    answer = finalize_chat_content(content, thinking, messages_for_llm)

    # Post-response validation (citation verdicts) — None when disabled or
    # settings are absent. Web chat never got verdicts before the loop
    # unification (2026-06-10 audit P2).
    validation = await validate_finalized_answer(answer, messages_for_llm, settings, chat_id)

    # Finalize LLM debug info with timing
    total_ms = round((time.monotonic() - stream_start) * 1000)
    llm_debug.timing["total_ms"] = total_ms
    llm_debug.final_messages = copy.deepcopy(messages_for_llm)
    llm_debug.response_content = answer.content
    llm_debug.iterations = total_tool_calls
    llm_debug.tool_calls_made = total_tool_calls

    # Optional payload shared by the persisted metadata and the done event.
    # Persisting citations / entity references / warnings means the source-text
    # blockquotes, entity pills, and truncation context survive a page refresh
    # (the chat history endpoint otherwise returns only raw [[cite:...]] markers).
    optional_payload = build_optional_payload(answer, warnings)
    if validation:
        optional_payload["validation"] = validation

    extra_metadata: dict[str, Any] = {**optional_payload, "llm_debug": llm_debug.to_dict()}

    pending_messages.append(
        chat_service.build_message(chat_id, "assistant", answer.content, extra_metadata)
    )
    # Single persistence point for the whole run — tool results buffered during
    # the loop plus this assistant answer, written together on success.
    chat_service.persist_messages(pending_messages)
    chat_service.update_chat_status(chat_id, "active")

    # Publish done event
    done_data: dict[str, Any] = {
        "status": done_status,
        "content": answer.content,
        "llm_debug": llm_debug.to_dict(),
        **optional_payload,
    }
    await publish_chat_event(chat_id, "done", done_data)

    logger.info(
        "chat_completion_done",
        chat_id=chat_id,
        status=done_status,
        content_length=len(answer.content),
        tool_calls_made=total_tool_calls,
    )

    return {
        "success": True,
        "chat_id": chat_id,
        "content_length": len(answer.content),
        "tool_calls_made": total_tool_calls,
    }
