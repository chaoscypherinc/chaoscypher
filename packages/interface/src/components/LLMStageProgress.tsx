// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * LLMStageProgress — shared rendering primitives for in-flight LLM stages.
 *
 * Used by SourceStageProgressList for the source list and source detail
 * page. Pure rendering, no source-specific knowledge.
 */

import { Box, Typography } from '@mui/material';
import type { ReactNode } from 'react';
import { formatDurationNullable } from '../utils/formatters';


interface LLMStageInlineProps {
  processed: number;
  total: number;
  itemNoun: string;             // "pages", "chunks"
  avgMs?: number | null;        // null = no ETA shown
}

/**
 * Inline "X/Y items · ~remaining" caption for an in-flight LLM stage.
 *
 * Renders nothing when total <= 0. Remaining-time suffix appears only
 * when avgMs is provided AND we still have items left to process.
 */
export function LLMStageInline({ processed, total, itemNoun, avgMs }: LLMStageInlineProps) {
  if (total <= 0) return null;
  const remaining =
    avgMs != null && avgMs > 0 && processed < total
      ? formatDurationNullable(((total - processed) * avgMs) / 1000)
      : null;
  return (
    <Typography
      variant="caption"
      sx={{ color: 'text.secondary', whiteSpace: 'nowrap' }}
    >
      {processed}/{total} {itemNoun}
      {remaining && <> · ~{remaining}</>}
    </Typography>
  );
}


interface LLMStageTooltipProps {
  label: string;                // "Vision processing", "MCP Entity Extraction"
  processed: number;
  total: number;
  itemNoun: string;
  avgMs?: number | null;
  children?: ReactNode;         // slot for stage-specific extras
}

/**
 * Tooltip body for an in-flight LLM stage. Compose with <Tooltip> at the
 * call site so the hover target stays the caller's choice.
 */
export function LLMStageTooltip({
  label, processed, total, itemNoun, avgMs, children,
}: LLMStageTooltipProps) {
  const remaining =
    avgMs != null && avgMs > 0 && processed < total
      ? formatDurationNullable(((total - processed) * avgMs) / 1000)
      : null;
  return (
    <Box sx={{ p: 0.5, minWidth: 180 }}>
      <Typography variant="caption" sx={{ display: 'block', fontWeight: 600, mb: 0.25 }}>
        {label}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block' }}>
        {processed} / {total} {itemNoun}
      </Typography>
      {avgMs != null && (
        <Typography variant="caption" sx={{ display: 'block' }}>
          Avg ~{(avgMs / 1000).toFixed(1)}s
        </Typography>
      )}
      {remaining && (
        <Typography variant="caption" sx={{ display: 'block' }}>
          ~{remaining} remaining
        </Typography>
      )}
      {children}
    </Box>
  );
}
