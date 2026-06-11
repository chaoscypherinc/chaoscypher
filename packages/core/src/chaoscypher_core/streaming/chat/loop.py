# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared chat tool-calling loop.

THE single implementation of the chat LLM + tool iteration loop, extracted
from the queued worker (the most feature-complete path after the 2026-06-10
Phase-0 fixes). Consumers plug in transport via :class:`ChatEventSink`
(Valkey pub/sub for the worker, console rendering for the CLI, a collector
for tests) and tool-approval decisions via :class:`ApprovalBroker`.

The loop OWNS: iteration control, duplicate-call filtering + guidance,
prompt-budget compaction, truncation warnings, unfulfilled-intent retry,
leaked-tool-call/empty-answer recovery, the forced final answer, tool
execution, event emission, and message buffering (``pending_messages``).

The CALLER owns: chat status transitions, persistence of the buffered
messages, done-event publication, spend recording, and queue/idempotency
semantics. Buffered messages are only ever persisted by the caller on the
success path — a run that raises leaves nothing behind, which is what makes
a transient-error retry idempotent.
"""

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from chaoscypher_core.utils.logging.app_config import get_logger


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pluggable transports
# ---------------------------------------------------------------------------


class ChatEventSink(Protocol):
    """Destination for loop events (content deltas, tool events, warnings).

    Implementations MUST NOT raise out of ``emit`` — event delivery is
    best-effort and must never break the chat turn itself.
    """

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Deliver one event."""
        ...


class ApprovalBroker(Protocol):
    """Source of tool-approval decisions (cross-process for the worker)."""

    async def request(
        self,
        chat_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        iteration: int,
    ) -> None:
        """Record that a decision is needed for this tool call."""
        ...

    async def wait(self, chat_id: str, tool_call_id: str, timeout_s: float) -> str:
        """Block until a decision arrives.

        Returns 'approve' / 'reject' / 'timeout' (anything but 'approve'
        denies the call — fail-closed).
        """
        ...


class AutoApproveBroker:
    """Fallback broker: every tool call is approved immediately.

    Used when a host process has no decision transport (and by tests that
    exercise the loop without approval semantics).
    """

    async def request(
        self,
        chat_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        iteration: int,
    ) -> None:
        """No-op — nothing to record."""

    async def wait(self, chat_id: str, tool_call_id: str, timeout_s: float) -> str:
        """Immediately approve."""
        return "approve"


SpendGuard = Callable[[], Awaitable[None]]
"""Raises (e.g. LLMSpendCapExceededError) when the spend cap is reached.

Invoked before the first LLM call. Exceptions propagate to the caller,
which classifies them (permanent caps fail the chat without retry).
"""


CancelCheck = Callable[[], Awaitable[bool]]
"""Returns True when the user requested cancellation of the running turn.

Checked at step boundaries (before each tool execution and before each
follow-up LLM call). Exceptions are treated as not-cancelled — a broken
transport keeps the turn running rather than killing it (fail-open).
"""


# ---------------------------------------------------------------------------
# Loop inputs / outputs
# ---------------------------------------------------------------------------


@dataclass
class LLMDebugInfo:
    """Debug information about LLM request/response for advanced UI display.

    Captures the raw input/output of LLM calls for debugging and transparency.
    (Moved from the deleted SSE handler when the loops were unified.)
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


@dataclass
class ChatLoopDeps:
    """Everything the shared loop needs from its host process."""

    chat_id: str
    provider: Any
    tool_executor: Any
    chat_service: Any
    settings: Settings
    sink: ChatEventSink
    approval: ApprovalBroker = field(default_factory=AutoApproveBroker)
    spend_guard: SpendGuard | None = None
    cancel_check: CancelCheck | None = None
    tools: list[dict[str, Any]] | None = None


@dataclass
class ChatLoopResult:
    """Outcome of one full chat turn through the shared loop."""

    content: str = ""
    thinking: str | None = None
    total_tool_calls: int = 0
    error_occurred: bool = False
    error_stage: str | None = None  # "initial_stream" | "tool_loop"
    cancelled: bool = False
    warnings: list[dict[str, str]] = field(default_factory=list)
    pending_messages: list[dict[str, Any]] = field(default_factory=list)
    tool_timings: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _ToolLoopState:
    """Mutable state for the tool-calling iteration loop."""

    content: str = ""
    thinking: str | None = None
    all_thinking_parts: list[str] = field(default_factory=list)
    total_tool_calls: int = 0
    iteration: int = 0


# ---------------------------------------------------------------------------
# Stream consumption
# ---------------------------------------------------------------------------


async def consume_llm_stream(
    llm_result: Any,
    sink: ChatEventSink,
    chat_id: str,
) -> tuple[str, str | None, list[Any] | None, bool, dict[str, Any] | None]:
    """Consume an LLM streaming response and forward events to the sink.

    Iterates over the async stream, forwarding content and thinking deltas,
    and returns the accumulated state.

    Args:
        llm_result: Async iterator of LLM response chunks.
        sink: Event destination.
        chat_id: Chat ID for logging.

    Returns:
        Tuple of (accumulated_content, thinking, tool_calls, stream_error,
        done_chunk). The raw done chunk carries ``finish_reason``/``usage``
        for truncation detection; None when the stream ended without one.

    """
    accumulated_content = ""
    thinking: str | None = None
    tool_calls: list[Any] | None = None
    stream_error = False
    done_chunk: dict[str, Any] | None = None

    try:
        async for chunk in llm_result:
            chunk_type = chunk.get("type")

            if chunk_type == "content":
                delta = chunk.get("delta", "")
                accumulated_content = chunk.get("accumulated", accumulated_content)
                await sink.emit(
                    "content",
                    {"delta": delta, "accumulated": accumulated_content},
                )

            elif chunk_type == "thinking_delta":
                thinking = chunk.get("accumulated", "")
                await sink.emit("thinking_delta", {"thinking": thinking})

            elif chunk_type == "error":
                error_msg = chunk.get("error", "Unknown LLM error")
                error_code = chunk.get("error_code", "LLM_ERROR")
                logger.error(
                    "chat_completion_stream_error",
                    chat_id=chat_id,
                    error=error_msg,
                    error_code=error_code,
                )
                await sink.emit(
                    "error",
                    {"error": error_msg, "error_code": error_code},
                )
                stream_error = True
                break

            elif chunk_type == "done":
                accumulated_content = chunk.get("content", accumulated_content)
                thinking = chunk.get("thinking", thinking)
                tool_calls = chunk.get("tool_calls")
                done_chunk = chunk
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

    return accumulated_content, thinking, tool_calls, stream_error, done_chunk


# ---------------------------------------------------------------------------
# Loop entry point
# ---------------------------------------------------------------------------


async def run_chat_tool_loop(
    messages_for_llm: list[dict[str, Any]],
    deps: ChatLoopDeps,
) -> ChatLoopResult:
    """Run one full chat turn: first LLM call, tool loop, final-answer recovery.

    Args:
        messages_for_llm: Message list for the turn (mutated in place as tool
            results and guidance are appended).
        deps: Host-process dependencies and transports.

    Returns:
        The turn outcome. ``pending_messages`` holds the buffered tool-result
        rows for the caller to persist on success.

    """
    from chaoscypher_core.streaming.chat.messages import detect_truncation_warnings

    result = ChatLoopResult()

    # Enforce the daily spend cap before spending tokens on this turn.
    # Exceptions (permanent caps) propagate to the caller.
    if deps.spend_guard is not None:
        await deps.spend_guard()

    llm_result = await deps.provider.chat(
        messages=messages_for_llm,
        tools=deps.tools,
        stream=True,
        enable_thinking=deps.settings.llm.thinking_for_chat,
    )
    logger.info("chat_completion_llm_call_initiated", chat_id=deps.chat_id)

    content, thinking, tool_calls, stream_error, done_chunk = await consume_llm_stream(
        llm_result, deps.sink, deps.chat_id
    )

    if stream_error:
        result.error_occurred = True
        result.error_stage = "initial_stream"
        return result

    # Detect silent truncation on the first call (output cut off by the token
    # budget, or prompt tokens pinned at the context window because the
    # provider dropped the head of the prompt) and tell the user.
    result.warnings = detect_truncation_warnings(done_chunk or {}, deps.settings, deps.chat_id)
    for warning in result.warnings:
        await deps.sink.emit("warning", dict(warning))

    result.content = content
    result.thinking = thinking

    if tool_calls:
        (
            result.content,
            result.thinking,
            result.total_tool_calls,
            tool_loop_error,
            result.cancelled,
        ) = await _handle_tool_loop(
            tool_calls=tool_calls,
            content=content,
            thinking=thinking,
            messages_for_llm=messages_for_llm,
            deps=deps,
            pending_messages=result.pending_messages,
            warnings=result.warnings,
            tool_timings=result.tool_timings,
        )
        if tool_loop_error:
            result.error_occurred = True
            result.error_stage = "tool_loop"

    return result


async def _handle_tool_loop(
    tool_calls: list[Any],
    content: str,
    thinking: str | None,
    messages_for_llm: list[Any],
    deps: ChatLoopDeps,
    pending_messages: list[dict[str, Any]],
    warnings: list[dict[str, str]],
    tool_timings: list[dict[str, Any]] | None = None,
) -> tuple[str, str | None, int, bool, bool]:
    """Execute the iterative tool-calling loop.

    Runs up to ``MAX_TOOL_ITERATIONS`` rounds of tool execution followed by
    follow-up LLM calls. Each round emits tool events, executes tools, and
    checks whether the LLM wants to call more tools.

    Args:
        tool_calls: Initial tool calls from the first LLM response.
        content: Accumulated content from the first LLM response.
        thinking: Thinking content from the first LLM response.
        messages_for_llm: Mutable message history.
        deps: Loop dependencies.
        pending_messages: Run-level buffer; tool-result messages are appended
            here (not persisted) and flushed on success by the caller.
        warnings: Run-level truncation-warning collector (mutated in place;
            one entry per kind per turn). Each newly collected warning is
            also emitted as a ``warning`` event.
        tool_timings: Optional collector for per-tool durations
            (name/duration_ms/tool_call_id), surfaced via llm_debug.

    Returns:
        Tuple of (final_content, final_thinking, total_tool_calls,
        error_occurred, cancelled).

    """
    from chaoscypher_core.streaming.chat.messages import (
        enforce_tool_loop_budget,
    )
    from chaoscypher_core.streaming.chat.tools import (
        _extract_tool_defaults,
        _filter_duplicate_tool_calls,
        _retry_unfulfilled_intent,
    )
    from chaoscypher_core.streaming.chat.utils import strip_thinking_tags

    chat_id = deps.chat_id
    state = _ToolLoopState(
        content=content,
        thinking=thinking,
        all_thinking_parts=[thinking] if thinking else [],
    )
    current_tool_calls: list[Any] | None = tool_calls
    error_occurred = False
    spend_capped = False
    cancelled = False
    tool_defaults = _extract_tool_defaults(deps.tools) if deps.tools else None
    executed_signatures: dict[str, int] = {}
    # Live settings, not import-frozen constants — the Settings-page knobs
    # actually bind (2026-06-10 audit: they were fake before this).
    max_iterations, max_total_calls = _tool_limits(deps.settings)

    while current_tool_calls and state.iteration < max_iterations:
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
        # Live phase marker for the UI (the frontend segments multi-phase
        # content on these).
        await deps.sink.emit(
            "iteration_progress",
            {
                "iteration": state.iteration,
                "max_iterations": max_iterations,
                "tool_count": batch_size,
            },
        )

        # Check total tool call limit
        if state.total_tool_calls > max_total_calls:
            logger.warning(
                "chat_completion_tool_limit_reached",
                chat_id=chat_id,
                total=state.total_tool_calls,
                limit=max_total_calls,
            )
            await deps.sink.emit(
                "warning",
                {"message": "Tool call limit reached", "limit": max_total_calls},
            )
            break

        # Skip calls already executed with identical arguments — local models
        # spin on the same search ("search_nodes Natasha" x4 in the live
        # repro), burning the iteration budget before ever answering.
        filtered_calls, duplicate_calls = _filter_duplicate_tool_calls(
            current_tool_calls,
            executed_signatures,
            chat_id,
            state.iteration,
            tool_defaults=tool_defaults,
        )
        if duplicate_calls:
            await deps.sink.emit(
                "cached_tool_calls",
                {"tool_calls": duplicate_calls, "iteration": state.iteration},
            )

        if filtered_calls:
            await deps.sink.emit(
                "tool_calls",
                {"tool_calls": filtered_calls, "iteration": state.iteration},
            )

            # Add assistant message with the executed tool calls to LLM context
            clean_content = strip_thinking_tags(state.content)
            messages_for_llm.append(
                {
                    "role": "assistant",
                    "content": clean_content,
                    "tool_calls": filtered_calls,
                }
            )

            # Execute each tool in the batch, gating behind user approval
            # when the configured policy demands it. Denied calls still get
            # their signature tracked so the model cannot loop on them.
            if await _run_tool_batch(
                filtered_calls=filtered_calls,
                iteration=state.iteration,
                executed_signatures=executed_signatures,
                tool_defaults=tool_defaults,
                messages_for_llm=messages_for_llm,
                pending_messages=pending_messages,
                tool_timings=tool_timings,
                warnings=warnings,
                deps=deps,
            ):
                cancelled = True
                break
        else:
            # Every call this round was a repeat — inject corrective guidance
            # (with the node IDs already found) instead of re-executing, and
            # let the follow-up call pick a different approach.
            _inject_duplicate_guidance(messages_for_llm, duplicate_calls, chat_id, state.iteration)

        # Keep the grown prompt inside the context window before the next LLM
        # call — otherwise Ollama silently drops the HEAD of the prompt
        # (system prompt + question first) and the answer degrades to a
        # confident fragment. Compacts older tool results in place.
        enforce_tool_loop_budget(messages_for_llm, deps.settings, chat_id)

        # Re-check the spend cap before every follow-up call — a multi-hop
        # turn can cross the cap mid-loop. Unlike the first-call check
        # (which fails the turn), a mid-loop cap ends the loop gracefully:
        # the gathered tool results still produce an answer below.
        if await _spend_capped_mid_loop(deps, state.iteration, warnings):
            spend_capped = True
            break

        # Step boundary: a user cancel lands here before the next LLM call.
        if await _cancel_requested_mid_loop(deps, state.iteration, warnings):
            cancelled = True
            break

        # Follow-up LLM call to check for more tools or final response
        followup_result = await deps.provider.chat(
            messages=messages_for_llm,
            tools=deps.tools,
            stream=True,
            enable_thinking=deps.settings.llm.thinking_for_tools,
        )

        (
            followup_content,
            followup_thinking,
            next_tool_calls,
            followup_error,
            followup_done,
        ) = await consume_llm_stream(followup_result, deps.sink, chat_id)

        # If follow-up errored, stop the tool loop (error event already emitted)
        if followup_error:
            error_occurred = True
            break

        # Surface truncation warnings detected on the follow-up call. One
        # event per kind per turn — a multi-round overflow would otherwise
        # repeat the same warning every iteration.
        await _collect_followup_warnings(followup_done, deps, state.iteration, warnings)

        _apply_followup_state(state, followup_content, followup_thinking)

        current_tool_calls = next_tool_calls

        # The LLM sometimes *describes* the next tool call ("Let me now
        # analyze the graph structure...") instead of emitting one, which
        # would otherwise finalize the fragment as the answer. Retry to
        # prompt it into actually calling the tool.
        if not current_tool_calls:
            retry_tool_calls = await _retry_unfulfilled_intent(
                followup_content=followup_content,
                iteration=state.iteration,
                messages_for_llm=messages_for_llm,
                chat_provider=deps.provider,
                available_tools=deps.tools,
                chat_id=chat_id,
                settings=deps.settings,
            )
            if retry_tool_calls:
                logger.info(
                    "chat_completion_unfulfilled_intent_retry",
                    chat_id=chat_id,
                    iteration=state.iteration,
                    retry_tool_count=len(retry_tool_calls),
                )
                current_tool_calls = retry_tool_calls

    # Final-content recovery makes one more LLM call — skipped after a
    # mid-loop spend cap (the cap is the reason the loop stopped; the
    # spend_cap warning explains a thin or apologetic answer) and after a
    # cancel (the user said stop; no further LLM spend).
    if not error_occurred and not spend_capped and not cancelled:
        state.content = await _resolve_final_content(
            content=state.content,
            messages_for_llm=messages_for_llm,
            deps=deps,
        )

    # Join all thinking parts for multi-step display
    final_thinking = (
        "\n\n---\n\n".join(state.all_thinking_parts) if state.all_thinking_parts else None
    )

    return state.content, final_thinking, state.total_tool_calls, error_occurred, cancelled


def _inject_duplicate_guidance(
    messages_for_llm: list[Any],
    duplicate_calls: list[Any],
    chat_id: str,
    iteration: int,
) -> None:
    """Append corrective guidance after an all-duplicates tool round.

    Args:
        messages_for_llm: Mutable message history.
        duplicate_calls: The filtered-out duplicate tool calls.
        chat_id: Chat ID for logging.
        iteration: Current iteration for logging.

    """
    from chaoscypher_core.streaming.chat.tools import _generate_duplicate_guidance

    dup_names = [d.get("function", {}).get("name", "unknown") for d in duplicate_calls]
    logger.warning(
        "chat_completion_all_tools_duplicate",
        chat_id=chat_id,
        iteration=iteration,
        duplicate_names=dup_names,
    )
    messages_for_llm.append(
        {
            "role": "user",
            "content": _generate_duplicate_guidance(messages_for_llm, dup_names),
        }
    )


def _tool_limits(settings: Any) -> tuple[int, int]:
    """Resolve (max_tool_iterations, max_total_tool_calls) from live settings.

    Unreadable/mocked settings fail OPEN to the schema defaults so partially
    mocked tests and degraded hosts keep today's behavior; the point of the
    live read is that the Settings-page knobs actually bind in production.

    Args:
        settings: Application settings (or a test double).

    Returns:
        Tuple of (max_tool_iterations, max_total_tool_calls).

    """
    from chaoscypher_core.services.chat.engine.constants import (
        MAX_TOOL_ITERATIONS,
        MAX_TOTAL_TOOL_CALLS,
    )

    chat = getattr(settings, "chat", None)
    iterations = getattr(chat, "max_tool_iterations", None)
    total = getattr(chat, "max_total_tool_calls", None)
    return (
        iterations if isinstance(iterations, int) and iterations > 0 else MAX_TOOL_ITERATIONS,
        total if isinstance(total, int) and total > 0 else MAX_TOTAL_TOOL_CALLS,
    )


def _apply_followup_state(
    state: _ToolLoopState,
    followup_content: str,
    followup_thinking: str | None,
) -> None:
    """Fold a follow-up call's content/thinking into the loop state.

    Args:
        state: Mutable loop state.
        followup_content: Content from the follow-up call (empty keeps the
            previous round's content).
        followup_thinking: Thinking from the follow-up call, if any.

    """
    if followup_content:
        state.content = followup_content
    if followup_thinking:
        state.all_thinking_parts.append(followup_thinking)


async def _run_tool_batch(
    filtered_calls: list[Any],
    iteration: int,
    executed_signatures: dict[str, int],
    tool_defaults: Any,
    messages_for_llm: list[Any],
    pending_messages: list[dict[str, Any]],
    tool_timings: list[dict[str, Any]] | None,
    warnings: list[dict[str, str]],
    deps: ChatLoopDeps,
) -> bool:
    """Execute one approved tool batch; True when a cancel landed mid-batch.

    Each tool is gated behind the approval policy and preceded by a cancel
    check — a multi-tool batch stops at the next tool boundary, keeping the
    results already gathered (they stay in ``pending_messages`` for the
    caller to persist).

    Args:
        filtered_calls: Tool calls left after duplicate filtering.
        iteration: Current loop iteration (for events/logging).
        executed_signatures: Signature tracker (mutated; denied calls are
            tracked too so the model cannot loop on them).
        tool_defaults: Schema-derived default arguments for signatures.
        messages_for_llm: Mutable message history.
        pending_messages: Run-level persistence buffer.
        tool_timings: Optional per-tool duration collector.
        warnings: Run-level warning collector (cancel appends here).
        deps: Loop dependencies.

    Returns:
        True when the batch was interrupted by a user cancel.

    """
    from chaoscypher_core.streaming.chat.tools import _track_tool_signature

    approval_mode, mutating_tools = _approval_policy(deps.settings)
    for tool_call in filtered_calls:
        # Step boundary: a user cancel lands between tool executions.
        if await _cancel_requested_mid_loop(deps, iteration, warnings):
            return True
        _track_tool_signature(tool_call, executed_signatures, tool_defaults)
        if approval_mode != "never-ask" and not await _approve_tool_call(
            tool_call=tool_call,
            approval_mode=approval_mode,
            mutating_tools=mutating_tools,
            iteration=iteration,
            messages_for_llm=messages_for_llm,
            pending_messages=pending_messages,
            deps=deps,
        ):
            continue
        await _execute_tool(
            tool_call=tool_call,
            deps=deps,
            messages_for_llm=messages_for_llm,
            iteration=iteration,
            pending_messages=pending_messages,
            tool_timings=tool_timings,
        )
    return False


async def _cancel_requested_mid_loop(
    deps: ChatLoopDeps,
    iteration: int,
    warnings: list[dict[str, str]],
) -> bool:
    """Check the cancel flag at a step boundary; True stops the loop.

    Emits and collects (once per turn) a ``cancelled`` warning so the user
    sees why the answer stops where it does, both live and after a reload.
    Check errors fail OPEN — a broken transport never kills a running turn.

    Args:
        deps: Loop dependencies (``cancel_check`` may be None).
        iteration: Current iteration for logging.
        warnings: Run-level warning collector (mutated in place).

    Returns:
        True when the user requested cancellation.

    """
    if deps.cancel_check is None:
        return False
    try:
        cancelled = bool(await deps.cancel_check())
    except Exception:
        logger.warning(
            "chat_completion_cancel_check_failed",
            chat_id=deps.chat_id,
            iteration=iteration,
        )
        return False
    if not cancelled:
        return False
    logger.info("chat_completion_cancelled", chat_id=deps.chat_id, iteration=iteration)
    cancel_warning = {
        "kind": "cancelled",
        "message": ("Answer stopped at your request — showing what was gathered so far."),
    }
    if all(existing["kind"] != "cancelled" for existing in warnings):
        warnings.append(cancel_warning)
        await deps.sink.emit("warning", dict(cancel_warning))
    return True


async def _spend_capped_mid_loop(
    deps: ChatLoopDeps,
    iteration: int,
    warnings: list[dict[str, str]],
) -> bool:
    """Run the spend guard between iterations; True when the cap was hit.

    A mid-loop cap appends (once) and emits a ``spend_cap`` warning so the
    user knows why the answer stopped where it did.

    Args:
        deps: Loop dependencies (guard may be None).
        iteration: Current iteration for logging.
        warnings: Run-level warning collector (mutated in place).

    Returns:
        True when the loop should stop spending.

    """
    if deps.spend_guard is None:
        return False
    try:
        await deps.spend_guard()
    except Exception:
        logger.warning(
            "chat_completion_spend_cap_mid_loop",
            chat_id=deps.chat_id,
            iteration=iteration,
        )
        cap_warning = {
            "kind": "spend_cap",
            "message": (
                "The daily LLM spend cap was reached mid-answer; "
                "finishing with the information gathered so far."
            ),
        }
        if all(existing["kind"] != "spend_cap" for existing in warnings):
            warnings.append(cap_warning)
            await deps.sink.emit("warning", dict(cap_warning))
        return True
    return False


async def _collect_followup_warnings(
    followup_done: dict[str, Any] | None,
    deps: ChatLoopDeps,
    iteration: int,
    warnings: list[dict[str, str]],
) -> None:
    """Collect + emit truncation warnings from a follow-up done chunk.

    Deduped by kind across the turn (mutates ``warnings`` in place).

    Args:
        followup_done: The follow-up call's raw done chunk (may be None).
        deps: Loop dependencies.
        iteration: Current iteration (included in the emitted payload).
        warnings: Run-level warning collector.

    """
    from chaoscypher_core.streaming.chat.messages import detect_truncation_warnings

    for warning in detect_truncation_warnings(followup_done or {}, deps.settings, deps.chat_id):
        if all(existing["kind"] != warning["kind"] for existing in warnings):
            warnings.append(warning)
            await deps.sink.emit("warning", {**warning, "iteration": iteration})


def _approval_policy(settings: Any) -> tuple[str, frozenset[str]]:
    """Resolve the tool-approval mode from settings.

    Unreadable settings (partially-mocked objects in tests, missing
    attributes) fail OPEN to ``never-ask`` — approval is a UX confirmation
    layer, and the system prompt's untrusted-data rules are the security
    backstop regardless of mode.

    Args:
        settings: Application settings (or a test double).

    Returns:
        Tuple of (mode, mutating-tool names).

    """
    mode = getattr(getattr(settings, "chat", None), "tool_approval", "never-ask")
    if mode not in ("always-ask", "ask-on-write"):
        return "never-ask", frozenset()
    raw = getattr(settings.chat, "mutating_tools", [])
    mutating = frozenset(raw) if isinstance(raw, (list, tuple, set, frozenset)) else frozenset()
    return mode, mutating


async def _approve_tool_call(
    tool_call: dict[str, Any],
    approval_mode: str,
    mutating_tools: frozenset[str],
    iteration: int,
    messages_for_llm: list[Any],
    pending_messages: list[dict[str, Any]],
    deps: ChatLoopDeps,
) -> bool:
    """Gate one tool call behind the user's approval decision.

    Emits ``tool_approval_required``, asks the broker, and waits for the
    decision. On denial (reject or timeout) a tool-response message is
    synthesized into the LLM history and the persistence buffer so the
    model knows the call was denied and the record survives reloads, and a
    ``tool_rejected`` event is emitted.

    Args:
        tool_call: The tool call under consideration.
        approval_mode: 'always-ask' or 'ask-on-write'.
        mutating_tools: Tool names gated by 'ask-on-write'.
        iteration: Current tool-loop iteration.
        messages_for_llm: Mutable message history.
        pending_messages: Run-level persistence buffer.
        deps: Loop dependencies.

    Returns:
        True when the tool may execute; False when it was denied.

    """
    function = tool_call.get("function", {})
    tool_name = function.get("name", "")
    tool_call_id = tool_call.get("id") or tool_call.get("tool_call_id", "")

    needs_approval = approval_mode == "always-ask" or tool_name in mutating_tools
    if not needs_approval or not tool_call_id:
        return True

    # Parse arguments for display in the approval dialog. Parse errors are
    # swallowed — the UI just needs something human-readable; the real parse
    # happens later in _execute_tool.
    raw_args = function.get("arguments", "{}")
    try:
        args_for_display = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except ValueError, TypeError:
        args_for_display = {}
    if not isinstance(args_for_display, dict):
        args_for_display = {"_raw": args_for_display}

    await deps.sink.emit(
        "tool_approval_required",
        {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": args_for_display,
            "iteration": iteration,
        },
    )
    await deps.approval.request(deps.chat_id, tool_call_id, tool_name, args_for_display, iteration)

    timeout = getattr(getattr(deps.settings, "chat", None), "tool_approval_timeout_seconds", 120)
    if not isinstance(timeout, (int, float)):
        timeout = 120
    decision = await deps.approval.wait(deps.chat_id, tool_call_id, float(timeout))

    if decision == "approve":
        return True

    # Synthesize a tool-response message and skip execution so the LLM
    # knows the call was denied and can continue the conversation.
    denied_content = f"User {decision}ed this tool call. Do not retry."
    messages_for_llm.append(
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": denied_content,
        }
    )
    pending_messages.append(
        deps.chat_service.build_message(
            deps.chat_id,
            "tool",
            denied_content,
            {"tool_call_id": tool_call_id, "name": tool_name},
        )
    )
    await deps.sink.emit(
        "tool_rejected",
        {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "decision": decision,
        },
    )
    logger.info(
        "chat_completion_tool_denied",
        chat_id=deps.chat_id,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        decision=decision,
        iteration=iteration,
    )
    return False


async def _resolve_final_content(
    content: str,
    messages_for_llm: list[Any],
    deps: ChatLoopDeps,
) -> str:
    """Turn a non-answer loop ending into usable final content.

    The loop can end (no more tool calls, or iterations exhausted) while the
    latest content is still not an answer: a "let me ..." narration (live
    failures d319be80/22e8683a), tool-call syntax leaked as text (live
    failure cb4c1618 — the model spoke the wrong tool-call dialect), or
    empty content with tool results available. Forces one no-tools call to
    turn the gathered tool results into an actual answer. On failure: a
    narration fragment is kept, but raw tool-call syntax is never shown —
    it is blanked so finalize applies the apology fallback.

    Args:
        content: Final content as the loop left it.
        messages_for_llm: Full message history including all tool results.
        deps: Loop dependencies.

    Returns:
        The resolved final content (possibly empty for unrecoverable leaks).

    """
    from chaoscypher_core.streaming.chat.tools import (
        contains_leaked_tool_call,
        is_intent_fragment,
    )

    is_leak = contains_leaked_tool_call(content)
    # Empty content after a tool loop is recoverable: the gathered tool
    # results are still in the history, so one no-tools call usually
    # produces the answer. Without tool results there is nothing to answer
    # from — keep the apology path.
    has_tool_results = any(m.get("role") == "tool" for m in messages_for_llm)
    is_empty = (not content or not content.strip()) and has_tool_results
    if not (is_leak or is_empty or is_intent_fragment(content)):
        return content

    forced_content = await _force_final_answer(messages_for_llm=messages_for_llm, deps=deps)
    if forced_content and not contains_leaked_tool_call(forced_content):
        return forced_content
    if is_leak or is_empty:
        return ""  # finalize applies the apology fallback
    return content


async def _force_final_answer(
    messages_for_llm: list[Any],
    deps: ChatLoopDeps,
) -> str:
    """One no-tools LLM call converting gathered tool results into an answer.

    Used when the tool loop ends on an unfulfilled-intent fragment. Content
    deltas stream to the client via the shared consume helper, so the user
    watches the real answer replace the narration.

    Args:
        messages_for_llm: Full message history including all tool results.
        deps: Loop dependencies.

    Returns:
        The forced answer content, or "" when the call errors or returns
        nothing (caller keeps the fragment).

    """
    logger.info("chat_completion_forced_final_answer", chat_id=deps.chat_id)
    forced_messages = [
        *messages_for_llm,
        {
            "role": "user",
            "content": (
                "STOP. Do not describe what you will do next. Based on the "
                "tool results you have already gathered above, provide your "
                "final answer to my original question NOW. If something could "
                "not be found, say so clearly."
            ),
        },
    ]
    try:
        forced_result = await deps.provider.chat(
            messages=forced_messages,
            tools=None,
            stream=True,
            enable_thinking=deps.settings.llm.thinking_for_chat,
        )
        content, _thinking, _tool_calls, stream_error, _done = await consume_llm_stream(
            forced_result, deps.sink, deps.chat_id
        )
    except Exception:
        logger.warning("chat_completion_forced_final_answer_failed", chat_id=deps.chat_id)
        return ""
    return "" if stream_error else content


async def _execute_tool(
    tool_call: dict[str, Any],
    deps: ChatLoopDeps,
    messages_for_llm: list[Any],
    iteration: int,
    pending_messages: list[dict[str, Any]],
    tool_timings: list[dict[str, Any]] | None = None,
) -> None:
    """Execute a single tool call, emit events, and buffer the result.

    Parses arguments from the tool call, runs the tool via the executor,
    emits start/result events, buffers the tool result message for
    end-of-run persistence, and appends it to the LLM message history.

    Args:
        tool_call: Tool call dict with ``function.name`` and
            ``function.arguments``.
        deps: Loop dependencies.
        messages_for_llm: Mutable message history.
        iteration: Current tool iteration number.
        pending_messages: Run-level buffer the tool result is appended to;
            flushed on success by the caller so a transient-error retry
            never duplicates it.
        tool_timings: Optional collector for this call's duration
            (name/duration_ms/tool_call_id), surfaced via llm_debug.

    """
    from chaoscypher_core.streaming.chat.utils import parse_tool_arguments

    chat_id = deps.chat_id
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

    await deps.sink.emit(
        "tool_start",
        {"tool": tool_name, "arguments": arguments, "iteration": iteration},
    )

    # Execute tool with timing
    tool_start = time.monotonic()
    result = await deps.tool_executor.execute_tool(tool_name, arguments)
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

    await deps.sink.emit(
        "tool_result",
        {
            "tool": tool_name,
            "result": result,
            "iteration": iteration,
            "tool_call_id": tool_call_id,
            "duration_ms": duration_ms,
        },
    )
    if tool_timings is not None:
        tool_timings.append(
            {"name": tool_name, "duration_ms": duration_ms, "tool_call_id": tool_call_id}
        )

    # Buffer the tool result (persisted at end-of-run, not now) so a
    # transient failure later in the loop leaves nothing to duplicate on retry.
    pending_messages.append(
        deps.chat_service.build_message(
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


__all__ = [
    "ApprovalBroker",
    "AutoApproveBroker",
    "ChatEventSink",
    "ChatLoopDeps",
    "ChatLoopResult",
    "LLMDebugInfo",
    "SpendGuard",
    "consume_llm_stream",
    "run_chat_tool_loop",
]
