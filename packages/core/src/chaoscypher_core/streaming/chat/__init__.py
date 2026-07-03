# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Streaming Chat Sub-Package.

SSE-formatted streaming chat response generation with tool calling,
citation processing, and response validation. Decomposes the streaming
pipeline into focused modules:

- citations: Chunk citation and entity reference extraction/enrichment
- loop: THE shared tool-calling loop (all chat surfaces)
- tools: Tool-call dedup/guidance/retry helpers used by the loop
- messages: Message building and context window management
- validation: Response grounding and citation reference validation
- utils: SSE formatting, provider setup, and shared helpers
"""

from chaoscypher_core.streaming.chat.citations import (
    CHUNK_CITATION_PATTERN,
    ENTITY_REFERENCE_PATTERN,
    ChunkCitationData,
    EntityRefData,
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
    relocate_grouped_citations,
    strip_duplicated_citation_text,
    strip_malformed_citations,
)
from chaoscypher_core.streaming.chat.events import (
    CachedToolCallsEvent,
    ChatSSEEnvelope,
    ChatSSEEvent,
    ContentEvent,
    ContextInfoEvent,
    DoneEvent,
    ErrorEvent,
    IterationProgressEvent,
    ThinkingDeltaEvent,
    ThinkingEvent,
    TimingUpdateEvent,
    ToolApprovalRequiredEvent,
    ToolCallsEvent,
    ToolRejectedEvent,
    ToolResultEvent,
    ToolStartEvent,
    WarningEvent,
)
from chaoscypher_core.streaming.chat.finalize import (
    FinalizedAnswer,
    build_optional_payload,
    finalize_chat_content,
)
from chaoscypher_core.streaming.chat.loop import (
    ApprovalBroker,
    AutoApproveBroker,
    ChatEventSink,
    ChatLoopDeps,
    ChatLoopResult,
    LLMDebugInfo,
    SpendGuard,
    consume_llm_stream,
    run_chat_tool_loop,
)
from chaoscypher_core.streaming.chat.messages import (
    ContextInfo,
    MessageBuildResult,
    build_messages_for_llm,
    log_messages_debug,
)
from chaoscypher_core.streaming.chat.sinks import (
    CollectingSink,
    ValkeyPubSubSink,
)
from chaoscypher_core.streaming.chat.tools import (
    MAX_TOOL_ITERATIONS,
    MAX_TOTAL_TOOL_CALLS,
)
from chaoscypher_core.streaming.chat.utils import (
    create_fallback_response,
    extract_thinking_from_tags,
    format_sse_event,
    get_model_name,
    parse_tool_arguments,
    setup_chat_providers,
    strip_thinking_tags,
)
from chaoscypher_core.streaming.chat.validation import (
    validate_citation_references,
    validate_response_grounding,
)


__all__ = [
    # Citations
    "CHUNK_CITATION_PATTERN",
    "ENTITY_REFERENCE_PATTERN",
    # Streaming
    "MAX_TOOL_ITERATIONS",
    "MAX_TOTAL_TOOL_CALLS",
    # Shared loop
    "ApprovalBroker",
    "AutoApproveBroker",
    # SSE event models
    "CachedToolCallsEvent",
    "ChatEventSink",
    "ChatLoopDeps",
    "ChatLoopResult",
    "ChatSSEEnvelope",
    "ChatSSEEvent",
    "ChunkCitationData",
    "CollectingSink",
    "ContentEvent",
    # Messages
    "ContextInfo",
    "ContextInfoEvent",
    "DoneEvent",
    "EntityRefData",
    "ErrorEvent",
    # Finalize
    "FinalizedAnswer",
    "IterationProgressEvent",
    "LLMDebugInfo",
    "MessageBuildResult",
    "SpendGuard",
    "ThinkingDeltaEvent",
    "ThinkingEvent",
    "TimingUpdateEvent",
    "ToolApprovalRequiredEvent",
    "ToolCallsEvent",
    "ToolRejectedEvent",
    "ToolResultEvent",
    "ToolStartEvent",
    "ValkeyPubSubSink",
    "WarningEvent",
    "_strip_blockquotes_before_citations",
    "_strip_inline_quotes_before_citations",
    "build_messages_for_llm",
    "build_optional_payload",
    "consume_llm_stream",
    "correct_mismatched_citations",
    # Utils
    "create_fallback_response",
    "enrich_chunk_citations_from_tool_results",
    "enrich_entity_references_from_tool_results",
    "extract_chunk_citations",
    "extract_entity_references",
    "extract_thinking_from_tags",
    "finalize_chat_content",
    "format_sse_event",
    "get_model_name",
    "inject_citations_for_uncited_paragraphs",
    "inject_citations_into_blockquotes",
    "log_messages_debug",
    "normalize_chunk_references",
    "parse_tool_arguments",
    "relocate_grouped_citations",
    "run_chat_tool_loop",
    "setup_chat_providers",
    "strip_duplicated_citation_text",
    "strip_malformed_citations",
    "strip_thinking_tags",
    # Validation
    "validate_citation_references",
    "validate_response_grounding",
]
