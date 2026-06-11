# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat SSE event models.

Pydantic-discriminated union for every event the chat event stream
(GET /chats/{id}/events) can deliver. Exported to TypeScript via the
OpenAPI schema so the frontend gets a typed discriminated union instead
of stringly-typed comparisons.

The event types mirror every ``sink.emit(`` call in the shared chat tool
loop (``streaming/chat/loop.py``) plus the bridge-level events emitted by
``features/chats/api.py``.

No runtime validation of emitted events is performed — this module is
a schema document consumed by the OpenAPI→TS codegen.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ContentEvent(BaseModel):
    """LLM response content delta and accumulated text."""

    type: Literal["content"] = "content"
    delta: str
    accumulated: str


class ThinkingEvent(BaseModel):
    """Complete thinking block emitted after a reasoning phase."""

    type: Literal["thinking"] = "thinking"
    thinking: str


class ThinkingDeltaEvent(BaseModel):
    """Incremental thinking delta during reasoning."""

    type: Literal["thinking_delta"] = "thinking_delta"
    thinking: str


class ToolCallsEvent(BaseModel):
    """List of tool calls the LLM wants to make in this iteration."""

    type: Literal["tool_calls"] = "tool_calls"
    tool_calls: list[dict[str, Any]]


class CachedToolCallsEvent(BaseModel):
    """Duplicate tool calls that were skipped (de-duplicated)."""

    type: Literal["cached_tool_calls"] = "cached_tool_calls"
    tool_calls: list[dict[str, Any]]


class ToolStartEvent(BaseModel):
    """Emitted when a single tool begins execution."""

    type: Literal["tool_start"] = "tool_start"
    tool: str
    arguments: dict[str, Any] | None = None
    iteration: int | None = None


class ToolResultEvent(BaseModel):
    """Tool execution result after a tool call completes."""

    type: Literal["tool_result"] = "tool_result"
    tool: str
    tool_call_id: str | None = None
    result: Any
    duration_ms: int | None = None


class TimingUpdateEvent(BaseModel):
    """Thinking-phase timing payload; extra fields allowed for forward compatibility."""

    type: Literal["timing_update"] = "timing_update"
    model_config = {"extra": "allow"}


class IterationProgressEvent(BaseModel):
    """Emitted at the start of each tool-calling iteration."""

    type: Literal["iteration_progress"] = "iteration_progress"
    iteration: int | None = None
    tool_count: int | None = None
    total_tool_calls: int | None = None


class DoneEvent(BaseModel):
    """Stream completed successfully; extra fields allowed for forward compatibility."""

    type: Literal["done"] = "done"
    model_config = {"extra": "allow"}


class ErrorEvent(BaseModel):
    """An error occurred during streaming."""

    type: Literal["error"] = "error"
    error: str
    error_code: str | None = None
    error_details: dict[str, Any] | None = None


class ContextInfoEvent(BaseModel):
    """Context window usage metadata; extra fields allowed for forward compatibility."""

    type: Literal["context_info"] = "context_info"
    model_config = {"extra": "allow"}


class WarningEvent(BaseModel):
    """Non-fatal warning (truncation, spend cap, tool-call limit, duplicates)."""

    type: Literal["warning"] = "warning"
    message: str
    kind: str | None = None
    iteration: int | None = None
    duplicates: list[str] | None = None


class ToolApprovalRequiredEvent(BaseModel):
    """A tool call is paused waiting for the user's approval decision."""

    type: Literal["tool_approval_required"] = "tool_approval_required"
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] | None = None
    iteration: int | None = None


class ToolRejectedEvent(BaseModel):
    """A gated tool call was denied (user rejection or timeout)."""

    type: Literal["tool_rejected"] = "tool_rejected"
    tool_call_id: str
    tool_name: str
    decision: str | None = None


ChatSSEEvent = (
    ContentEvent
    | ThinkingEvent
    | ThinkingDeltaEvent
    | ToolCallsEvent
    | CachedToolCallsEvent
    | ToolStartEvent
    | ToolResultEvent
    | TimingUpdateEvent
    | IterationProgressEvent
    | DoneEvent
    | ErrorEvent
    | ContextInfoEvent
    | WarningEvent
    | ToolApprovalRequiredEvent
    | ToolRejectedEvent
)
"""Discriminated union over all 15 chat SSE event types."""


class ChatSSEEnvelope(BaseModel):
    """Named wrapper so OpenAPI emits ChatSSEEvent as a schema component."""

    event: ChatSSEEvent = Field(..., discriminator="type")
