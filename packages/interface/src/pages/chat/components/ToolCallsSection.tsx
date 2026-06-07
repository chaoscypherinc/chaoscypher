// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tool activity section listing executed and cached tool calls.
 *
 * Displays a bordered panel with:
 * - Executed tool calls (teal dots, expandable I/O via ToolCallItem)
 * - Dashed separator between executed and cached sections
 * - Cached/deduplicated tool calls (grey dots via CachedToolCallItem)
 */

import { Box, Typography } from '@mui/material';
import { CardColors } from '../../../theme/cardStyles';
import ToolCallItem from './ToolCallItem';
import CachedToolCallItem from './CachedToolCallItem';
import { formatToolArgs } from './message-utils';
import type { ToolCall, ToolResultLike, ToolTimingEntry } from './message-utils';

interface ToolCallsSectionProps {
  /** Executed tool calls from the message. */
  toolCalls: ToolCall[];
  /** Cached/deduplicated tool calls from the message. */
  cachedToolCalls: ToolCall[];
  /** Tool result messages for matching against tool_calls. */
  toolResults: ToolResultLike[];
  /** Per-tool timing entries from LLM debug info. */
  toolTimings?: ToolTimingEntry[];
}

export default function ToolCallsSection({ toolCalls, cachedToolCalls, toolResults, toolTimings }: ToolCallsSectionProps) {
  return (
    <Box
      sx={{
        mt: 1.5,
        pl: 2,
        borderLeft: '1px dashed rgba(0, 191, 165, 0.3)',
        width: '100%',
        maxHeight: '400px',
        overflow: 'auto',
        boxSizing: 'border-box',
      }}
    >
      <Typography variant="caption" sx={{ color: CardColors.info, fontWeight: 'bold', display: 'block', mb: 0.5 }}>
        Tool Activity
      </Typography>
      {/* Executed tool calls (teal dots, expandable) */}
      {toolCalls.map((call, idx) => {
        const toolCallId = call.id;
        const toolName = call.function?.name || 'Unknown tool';
        const matchingResult = toolResults.find(r =>
          r.tool_call_id === toolCallId ||
          r.extra_metadata?.tool_call_id === toolCallId
        );

        const compactArgs = formatToolArgs(call);

        const toolTimingEntry = toolTimings?.find(
          (t) => t.tool_call_id === toolCallId
        );

        return (
          <ToolCallItem
            key={toolCallId || `tool-call-${idx}`}
            toolName={toolName}
            compactArgs={compactArgs}
            call={call}
            matchingResult={matchingResult}
            isLast={cachedToolCalls.length === 0 && idx === toolCalls.length - 1}
            durationMs={toolTimingEntry?.duration_ms}
          />
        );
      })}
      {/* Dashed separator between executed and cached */}
      {toolCalls.length > 0 && cachedToolCalls.length > 0 && (
        <Box sx={{
          my: 0.5,
          borderBottom: '1px dashed',
          borderColor: 'text.disabled',
          opacity: 0.4,
        }} />
      )}
      {/* Cached tool calls (grey dots, not expandable) */}
      {cachedToolCalls.map((call, idx) => {
        const toolName = call.function?.name || 'Unknown tool';
        const compactArgs = formatToolArgs(call);

        return (
          <CachedToolCallItem
            key={`cached-tool-call-${idx}`}
            toolName={toolName}
            compactArgs={compactArgs}
            isLast={idx === cachedToolCalls.length - 1}
          />
        );
      })}
    </Box>
  );
}
