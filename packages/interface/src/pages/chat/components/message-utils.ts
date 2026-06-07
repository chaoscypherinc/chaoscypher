// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared types and utility functions for MessageBubble sub-components.
 *
 * Provides tool call interfaces, pattern-matching constants for
 * confirmation/approval/proposal detection, and formatting helpers.
 */

// ---------------------------------------------------------------------------
// Tool call types
// ---------------------------------------------------------------------------

export interface ToolCall {
  id?: string;
  function?: {
    name?: string;
    arguments?: string | Record<string, unknown>;
    description?: string;
  };
}

export interface ToolResultLike {
  tool_call_id?: string;
  content?: string;
  extra_metadata?: { tool_call_id?: string };
}

export interface ToolTimingEntry {
  tool_call_id?: string;
  duration_ms?: number;
}

export interface DebugMessage {
  role: string;
  content?: string;
}

// ---------------------------------------------------------------------------
// Utility: format tool call arguments into a compact display string
// ---------------------------------------------------------------------------

/** Parse tool call arguments into a compact `key=value, ...` string. */
export function formatToolArgs(call: ToolCall): string {
  const args = call.function?.arguments;
  if (!args) return '';
  try {
    const parsed = typeof args === 'string' ? JSON.parse(args) : args;
    return Object.entries(parsed)
      .map(([key, val]) => {
        const strVal = typeof val === 'string' ? val : JSON.stringify(val);
        return key === 'query' ? strVal : `${key}=${strVal}`;
      })
      .join(', ');
  } catch {
    return String(args);
  }
}

// ---------------------------------------------------------------------------
// Confirmation detection patterns
// ---------------------------------------------------------------------------

/** Past-tense phrases indicating the AI already took action. */
export const CONFIRMATION_PATTERNS = [
  "i've",
  "i have created",
  "i have updated",
  "i have deleted",
  "successfully created",
  "successfully updated",
  "successfully deleted",
  "done",
  "completed",
  "finished",
];

/** Question phrases indicating the AI is asking for permission. */
export const APPROVAL_PATTERNS = [
  "should i",
  "would you like me to",
  "would you like",
  "do you want me to",
  "shall i",
  "may i proceed",
  "can i proceed",
  "would you prefer",
];

/** Proposal phrases indicating the AI is announcing a planned action. */
export const PROPOSAL_PATTERNS = [
  "let's create",
  "let's update",
  "let's delete",
  "let's add",
  "i will create",
  "i'll create",
  "i will update",
  "i'll update",
  "i will delete",
  "i'll delete",
];

/** Check whether `text` contains any of the given patterns. */
export const matchesAny = (text: string, patterns: string[]) =>
  patterns.some(p => text.includes(p));
