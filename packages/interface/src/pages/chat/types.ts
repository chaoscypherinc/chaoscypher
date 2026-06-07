// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { ChatMessage, ChunkCitationMap, EntityReferenceMap, LLMDebugInfo } from '../../types';

/**
 * Validation result from post-response grounding check.
 */
export interface ValidationResult {
  verdict: 'correct' | 'wrong' | 'partial' | 'skipped' | 'error';
  reason: string;
  better_passage?: string | null;
  per_citation?: Record<string, { verdict: string; reason: string }>;
}

/**
 * Extended ChatMessage with flattened referenced_entities, chunk_citations, and llm_debug fields.
 * Used throughout the chat UI after normalization from the raw API response.
 */
export interface ExtendedChatMessage extends ChatMessage {
  referenced_entities?: EntityReferenceMap;
  chunk_citations?: ChunkCitationMap;
  cached_tool_calls?: unknown[];
  llm_debug?: LLMDebugInfo;
  validation?: ValidationResult;
}
