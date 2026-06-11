// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

// Chat domain type definitions for Chaos Cypher frontend

/**
 * Summary of an entity reference for interactive display in chat.
 * Contains data needed to render clickable entity chips with hover cards.
 */
export interface EntityReferenceSummary {
  id: string;
  type: 'node' | 'edge';
  label: string;
  template_id?: string | null;
  template_name?: string | null;
  properties?: Record<string, unknown> | null;
  incoming_count?: number | null;
  outgoing_count?: number | null;
  // Additional fields for richer hover cards
  title?: string | null;
  name?: string | null;
  description?: string | null;
  entity_type?: string | null;  // The node/edge type (e.g., "Person", "Concept")
  // Edge-specific
  source_node_id?: string | null;
  target_node_id?: string | null;
}

/**
 * Map of entity ID to reference summary data.
 */
export type EntityReferenceMap = Record<string, EntityReferenceSummary>;

/**
 * Summary of a chunk citation for inline display in chat.
 * Contains data needed to render hoverable citation chips with sentence previews.
 */
export interface ChunkCitationSummary {
  chunk_id: string;
  sentence_refs: string;       // e.g. "S3" or "S1,S2"
  label: string;               // Filename from LLM output
  sentence_text?: string | null; // Resolved sentence text for tooltip
  source_id?: string | null;
  page_number?: number | null;
  validation_verdict?: 'correct' | 'wrong' | null; // Per-citation grounding verdict
  has_vision_image?: boolean;  // True if chunk contains vision-described image content
}

/**
 * Map of chunk ID to chunk citation summary data.
 */
export type ChunkCitationMap = Record<string, ChunkCitationSummary>;

/**
 * Per-tool-call timing information.
 */
export interface ToolTiming {
  name: string;
  args_preview: string;
  duration_ms: number;
  iteration: number;
  tool_call_id?: string;
}

/**
 * Performance timing data from the streaming pipeline.
 */
export interface TimingInfo {
  total_ms?: number;
  time_to_first_token_ms?: number;
  thinking_ms?: number;
  generation_ms?: number;
  output_tokens?: number;
  tokens_per_sec?: number;
  tool_calls?: ToolTiming[];
}

/**
 * Debug information about LLM request/response for advanced UI display.
 * Captures the raw input/output of LLM calls for debugging and transparency.
 */
export interface LLMDebugInfo {
  /** LLM provider name (e.g., 'ollama', 'openai', 'anthropic') */
  provider: string;
  /** Model name/ID used for the request */
  model: string;
  /** Initial messages sent to the LLM (before any tool modifications) */
  initial_messages: Array<{
    role: string;
    content: string;
    tool_calls?: unknown[];
    tool_call_id?: string;
  }>;
  /** Available tools provided to the LLM */
  tools: unknown[];
  /** Final messages after all tool iterations */
  final_messages: Array<{
    role: string;
    content: string;
    tool_calls?: unknown[];
    tool_call_id?: string;
  }>;
  /** Final response content from the LLM */
  response_content: string;
  /** Total number of tool calls made during the conversation */
  tool_calls_made: number;
  /** Number of tool calling iterations */
  iterations: number;
  /** Performance timing data */
  timing?: TimingInfo;
}

/**
 * Context window usage information from the backend.
 * Used to show which messages are in/out of context.
 */
export interface ContextInfo {
  /** Total messages in the chat (excluding filtered) */
  total_messages: number;
  /** Number of messages included in LLM context */
  messages_in_context: number;
  /** Index of first message in context (messages before are greyed out) */
  first_in_context_index: number;
  /** Tokens used for history */
  tokens_used: number;
  /** Tokens available for history */
  tokens_available: number;
  /** Total context window size */
  context_window: number;
  /** LLM provider name */
  provider: string;
  /** Model name */
  model: string;
}

/**
 * Backend-detected problem with a streamed response (e.g. the answer was cut
 * off by the output limit, or the prompt overflowed the model context window).
 */
export interface ChatStreamWarning {
  kind: string;
  message: string;
}

export interface ChatMessage {
  /** Persisted message id (absent on optimistic rows not yet saved). */
  id?: string;
  role: string;
  content: string;
  tool_calls?: unknown[];
  tool_call_id?: string;
  name?: string;
  thinking?: string; // Model's internal reasoning/thinking
  extra_metadata?: {
    tool_call_id?: string;
    name?: string;
    tool_calls?: unknown[];
    cached_tool_calls?: unknown[];
    thinking?: string;
    referenced_entities?: EntityReferenceMap;
    /** Legacy key written by the queued worker 2026-06-09..10; superseded by referenced_entities. */
    entity_references?: EntityReferenceMap;
    chunk_citations?: ChunkCitationMap;
    llm_debug?: LLMDebugInfo;
    validation?: {
      verdict: 'correct' | 'wrong' | 'partial' | 'skipped' | 'error';
      reason: string;
      better_passage?: string | null;
      per_citation?: Record<string, { verdict: string; reason: string }>;
    };
    warnings?: ChatStreamWarning[];
  };
}

export interface ChatMetadata {
  id: string;
  title: string;
  status: string;
  message_count: number;
  source_ids?: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface Chat {
  id: string;
  title: string;
  status: string;
  messages: ChatMessage[];
  message_count: number;
  source_ids?: string[] | null;
  created_at: string;
  updated_at: string;
}

/**
 * Pending tool-approval request emitted by the backend over SSE.
 *
 * Populated from a `tool_approval_required` event. While a request is
 * pending, the chat stream is blocked server-side waiting for the user's
 * decision via `POST /chats/{id}/tool_decision`.
 */
export interface PendingToolApproval {
  /** Tool-call ID the backend is waiting on. Unique per chat turn. */
  tool_call_id: string;
  /** Human-readable tool name (e.g. `create_node`). */
  tool_name: string;
  /** Parsed arguments object the LLM wants to pass. */
  arguments: Record<string, unknown>;
  /** 1-indexed tool-calling iteration within the current turn. */
  iteration: number;
  /** ISO timestamp the request arrived on the client. */
  received_at: string;
}

/**
 * Structured error information from LLM providers.
 * Provides categorized errors with user-friendly messages and suggestions.
 */
export interface ChatError {
  /** Human-readable error message */
  message: string;
  /** Machine-readable error code for categorization */
  code: string;
  /** Additional error context and details */
  details: {
    provider?: string;
    model?: string;
    is_retryable?: boolean;
    suggested_action?: string;
    retry_after?: number;
    quota_exceeded?: boolean;
    filter_type?: string;
    original_error?: string;
    [key: string]: unknown;
  };
}
