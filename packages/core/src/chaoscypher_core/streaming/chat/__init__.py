# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Streaming Chat Sub-Package.

SSE-formatted streaming chat response generation with tool calling,
citation processing, and response validation. Decomposes the streaming
pipeline into focused modules:

- handler: Main stream_chat_response orchestration and finalization
- citations: Chunk citation and entity reference extraction/enrichment
- tools: Tool call execution, deduplication, and follow-up handling
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
    strip_duplicated_citation_text,
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
    ToolCallsEvent,
    ToolResultEvent,
    ToolStartEvent,
    WarningEvent,
)
from chaoscypher_core.streaming.chat.handler import (
    LLMDebugInfo,
    _save_done_chunk_timing,
    stream_chat_response,
)
from chaoscypher_core.streaming.chat.messages import (
    ContextInfo,
    MessageBuildResult,
    build_messages_for_llm,
    log_messages_debug,
)
from chaoscypher_core.streaming.chat.tools import (
    MAX_TOOL_ITERATIONS,
    MAX_TOTAL_TOOL_CALLS,
    ToolCallingState,
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
    # SSE event models
    "CachedToolCallsEvent",
    "ChatSSEEnvelope",
    "ChatSSEEvent",
    "ChunkCitationData",
    "ContentEvent",
    # Messages
    "ContextInfo",
    "ContextInfoEvent",
    "DoneEvent",
    "EntityRefData",
    "ErrorEvent",
    "IterationProgressEvent",
    "LLMDebugInfo",
    "MessageBuildResult",
    "ThinkingDeltaEvent",
    "ThinkingEvent",
    "TimingUpdateEvent",
    "ToolCallingState",
    "ToolCallsEvent",
    "ToolResultEvent",
    "ToolStartEvent",
    "WarningEvent",
    "_save_done_chunk_timing",
    "_strip_blockquotes_before_citations",
    "_strip_inline_quotes_before_citations",
    "build_messages_for_llm",
    "correct_mismatched_citations",
    # Utils
    "create_fallback_response",
    "enrich_chunk_citations_from_tool_results",
    "enrich_entity_references_from_tool_results",
    "extract_chunk_citations",
    "extract_entity_references",
    "extract_thinking_from_tags",
    "format_sse_event",
    "get_model_name",
    "inject_citations_for_uncited_paragraphs",
    "inject_citations_into_blockquotes",
    "log_messages_debug",
    "normalize_chunk_references",
    "parse_tool_arguments",
    "setup_chat_providers",
    "stream_chat_response",
    "strip_duplicated_citation_text",
    "strip_thinking_tags",
    # Validation
    "validate_citation_references",
    "validate_response_grounding",
]
