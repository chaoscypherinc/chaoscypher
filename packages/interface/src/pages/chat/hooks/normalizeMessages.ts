// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Message normalization utility for the chat UI.
 *
 * Flattens nested `extra_metadata` fields from the API response into
 * top-level properties on each message, providing a consistent access
 * pattern for the rendering layer.
 */

import type { ChatMessage } from '../../../types';
import type { ExtendedChatMessage } from '../types';

/**
 * Normalize messages from API to flatten extra_metadata fields into top-level
 * properties for consistent access throughout the chat UI.
 */
export function normalizeMessages(messages: ChatMessage[]): ExtendedChatMessage[] {
  return messages.map(msg => {
    const normalized: ExtendedChatMessage = { ...msg };
    if (msg.extra_metadata) {
      // Flatten tool_calls from extra_metadata if not already present
      if (msg.extra_metadata.tool_calls && !normalized.tool_calls) {
        normalized.tool_calls = msg.extra_metadata.tool_calls;
      }
      // Flatten tool_call_id from extra_metadata if not already present
      if (msg.extra_metadata.tool_call_id && !normalized.tool_call_id) {
        normalized.tool_call_id = msg.extra_metadata.tool_call_id;
      }
      // Flatten name from extra_metadata if not already present
      if (msg.extra_metadata.name && !normalized.name) {
        normalized.name = msg.extra_metadata.name;
      }
      // Flatten thinking from extra_metadata if not already present
      if (msg.extra_metadata.thinking && !normalized.thinking) {
        normalized.thinking = msg.extra_metadata.thinking;
      }
      // Flatten referenced_entities from extra_metadata
      if (msg.extra_metadata.referenced_entities) {
        normalized.referenced_entities = msg.extra_metadata.referenced_entities;
      }
      // Flatten chunk_citations from extra_metadata
      if (msg.extra_metadata.chunk_citations) {
        normalized.chunk_citations = msg.extra_metadata.chunk_citations;
      }
      // Flatten cached_tool_calls from extra_metadata
      if (msg.extra_metadata.cached_tool_calls) {
        normalized.cached_tool_calls = msg.extra_metadata.cached_tool_calls;
      }
      // Flatten llm_debug from extra_metadata
      if (msg.extra_metadata.llm_debug) {
        normalized.llm_debug = msg.extra_metadata.llm_debug;
      }
      // Flatten validation from extra_metadata
      if (msg.extra_metadata.validation) {
        normalized.validation = msg.extra_metadata.validation;
      }
      // Merge per_citation verdicts into chunk citations for persisted messages
      const perCitation = msg.extra_metadata.validation?.per_citation;
      if (perCitation && normalized.chunk_citations) {
        for (const [id, cite] of Object.entries(normalized.chunk_citations)) {
          const v = (perCitation as Record<string, { verdict: string }>)[id]?.verdict;
          cite.validation_verdict = v === 'correct' ? 'correct' : v === 'wrong' ? 'wrong' : null;
        }
      }
    }
    return normalized;
  });
}
