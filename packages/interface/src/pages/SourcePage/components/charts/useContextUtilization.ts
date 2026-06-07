// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Custom hook for context utilization chart data.
 *
 * Encapsulates settings fetching, actual-stats derivation (from backend
 * aggregates or fallback task-level calculation), planned-breakdown
 * computation, segment/color definitions, and marker-line positions.
 */

import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTheme, alpha } from '@mui/material';
import type { ExtractionTask, ExtractionTaskStats } from '../../../../types';
import { ContextColors } from '../../../../theme/colors';
import { settingsApi } from '../../../../services/api/settings';
import { calculateContextBreakdown, type BreakdownResult } from '../../../../components';
import { logger } from '../../../../utils/logger';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ActualStats {
  minUsed: number;
  maxUsed: number;
  avgUsed: number;
  minInput: number;
  minOutput: number;
  maxInput: number;
  maxOutput: number;
  avgInput: number;
  avgOutput: number;
  contextWindow: number;
  minUtilization: number;
  maxUtilization: number;
  avgUtilization: number;
  tasksWithData: number;
}

export interface Segment {
  key: string;
  label: string;
  tokens: number;
  percentage: number;
  color: string;
  description: string;
}

export interface SegmentColors {
  system: string;
  input: string;
  output: string;
  outputCap: string;
  buffer: string;
}

interface ContextUtilizationData {
  /** Null when no token data is available yet. */
  actualStats: ActualStats | null;
  /** Planned context breakdown (only meaningful when actualStats is non-null). */
  planned: BreakdownResult;
  /** Bar segments in render order. */
  segments: Segment[];
  /** Resolved color palette. */
  colors: SegmentColors;
  /** Context window size used for calculations. */
  contextWindow: number;
  /** Max output token cap from settings or derived. */
  maxOutputTokens: number;
  /** Whether to show the output-cap indicator line. */
  showOutputCapLine: boolean;
  /**
   * Marker-line positions as percentages of the context window, clamped to
   * [0, 100] so a line never overflows past the bar. When usage exceeds the
   * window the clamped position pins to 100 and ``overBudget`` is set.
   */
  linePositions: {
    min: number;
    avg: number;
    max: number;
  };
  /**
   * True when peak actual usage exceeded the configured context window
   * (max utilization > 100%). Drives the over-budget badge.
   */
  overBudget: boolean;
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Convert backend ExtractionTaskStats to ActualStats format.
 * Uses SQL aggregates computed over ALL tasks, not just the current page.
 */
function statsToActualStats(stats: ExtractionTaskStats): ActualStats | null {
  if (
    stats.min_total_tokens == null ||
    stats.max_total_tokens == null ||
    stats.avg_total_tokens == null ||
    stats.context_window == null
  ) {
    return null;
  }

  return {
    minUsed: stats.min_total_tokens,
    maxUsed: stats.max_total_tokens,
    avgUsed: stats.avg_total_tokens,
    minInput: stats.min_input_tokens ?? 0,
    minOutput: stats.min_output_tokens ?? 0,
    maxInput: stats.max_input_tokens ?? 0,
    maxOutput: stats.max_output_tokens ?? 0,
    avgInput: stats.avg_input_tokens ?? 0,
    avgOutput: stats.avg_output_tokens ?? 0,
    contextWindow: stats.context_window,
    minUtilization: stats.min_utilization ?? 0,
    maxUtilization: stats.max_utilization ?? 0,
    avgUtilization: stats.avg_utilization ?? 0,
    tasksWithData: stats.total_tasks,
  };
}

/**
 * Calculate stats from paginated tasks (fallback when stats not available).
 */
function calculateActualStatsFromTasks(tasks: ExtractionTask[]): ActualStats | null {
  const tasksWithTokens = tasks.filter(
    (t) =>
      t.input_tokens != null &&
      t.output_tokens != null &&
      t.context_window_available != null
  );

  if (tasksWithTokens.length === 0) {
    return null;
  }

  const inputTokens = tasksWithTokens.map((t) => t.input_tokens!);
  const outputTokens = tasksWithTokens.map((t) => t.output_tokens!);
  const totalUsed = tasksWithTokens.map((t) => t.input_tokens! + t.output_tokens!);
  const contextWindow = tasksWithTokens[0].context_window_available!;

  const minInput = Math.min(...inputTokens);
  const minOutput = Math.min(...outputTokens);
  const minUsed = Math.min(...totalUsed);
  const maxInput = Math.max(...inputTokens);
  const maxOutput = Math.max(...outputTokens);
  const maxUsed = Math.max(...totalUsed);
  const avgInput = Math.round(inputTokens.reduce((a, b) => a + b, 0) / inputTokens.length);
  const avgOutput = Math.round(outputTokens.reduce((a, b) => a + b, 0) / outputTokens.length);
  const avgUsed = Math.round(totalUsed.reduce((a, b) => a + b, 0) / totalUsed.length);

  const minUtilization = (minUsed / contextWindow) * 100;
  const maxUtilization = (maxUsed / contextWindow) * 100;
  const avgUtilization = (avgUsed / contextWindow) * 100;

  return {
    minUsed,
    maxUsed,
    avgUsed,
    minInput,
    minOutput,
    maxInput,
    maxOutput,
    avgInput,
    avgOutput,
    contextWindow,
    minUtilization,
    maxUtilization,
    avgUtilization,
    tasksWithData: tasksWithTokens.length,
  };
}

// ---------------------------------------------------------------------------
// Shared utility
// ---------------------------------------------------------------------------

/**
 * Map a utilization percentage to a MUI severity color name.
 */
export function getUtilizationColor(util: number): 'success' | 'warning' | 'error' {
  if (util > 90) return 'error';
  if (util > 75) return 'warning';
  return 'success';
}

/**
 * Format a number with locale-aware thousand separators.
 */
export function formatNumber(n: number): string {
  return n.toLocaleString();
}

/** Clamp a percentage to the renderable [0, 100] range. */
function clampPercent(value: number): number {
  return Math.min(100, Math.max(0, value));
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Computes all derived data needed by the context utilization chart.
 *
 * @param tasks - Current page of extraction tasks (fallback data source).
 * @param stats - Backend-aggregated stats across all tasks (preferred).
 * @returns Fully computed chart data ready for rendering.
 */
export function useContextUtilization(
  tasks: ExtractionTask[],
  stats: ExtractionTaskStats | null | undefined,
): ContextUtilizationData {
  const theme = useTheme();

  const settingsQuery = useQuery({
    queryKey: ['settings'] as const,
    queryFn: () => settingsApi.get(),
  });
  const settings = settingsQuery.data ?? null;

  // Preserve the legacy fetch's logging: surface settings-load failures via
  // the logger without crashing the chart (it falls back to stored values).
  useEffect(() => {
    if (settingsQuery.error) {
      logger.error(settingsQuery.error);
    }
  }, [settingsQuery.error]);

  // Prefer stats (aggregates over ALL tasks) over task-based calculation
  const actualStats = useMemo(() => {
    if (stats) {
      return statsToActualStats(stats);
    }
    return calculateActualStatsFromTasks(tasks);
  }, [stats, tasks]);

  // Derive all computed values from actualStats + settings
  const derived = useMemo(() => {
    // Prefer the context window stored at extraction time. The backend
    // computes the Min/Avg/Max utilization chips against that stored window,
    // so the bar's marker lines must use the same denominator or the two
    // disagree (and either can read past 100%) whenever ``ai_context_window``
    // is changed after extraction. Fall back to the live setting only when
    // there is no actual usage data yet (planning-only view).
    const contextWindow = actualStats?.contextWindow || settings?.llm?.ai_context_window || 0;
    const maxOutputTokens =
      settings?.llm?.extraction_max_tokens || Math.floor(contextWindow * 0.25);
    const outputPerChunk = settings?.chunking?.output_tokens_per_chunk || 2000;
    const groupSize = settings?.chunking?.group_size || 4;
    const inputPerChunk = settings?.chunking?.small_chunk_size
      ? Math.floor(settings.chunking.small_chunk_size / 4)
      : 150;

    const planned = calculateContextBreakdown(
      contextWindow,
      maxOutputTokens,
      inputPerChunk,
      outputPerChunk,
      groupSize,
    );

    const colors: SegmentColors = {
      ...ContextColors,
      buffer: alpha(theme.palette.grey[300], 0.3),
    };

    const segments: Segment[] = [
      {
        key: 'system',
        label: 'System',
        tokens: planned.systemTokens,
        percentage: planned.percentages.system,
        color: colors.system,
        description: 'System prompt & extraction instructions',
      },
      {
        key: 'input',
        label: 'Input',
        tokens: planned.inputTokens,
        percentage: planned.percentages.input,
        color: colors.input,
        description: `${planned.chunks} chunks @ ${inputPerChunk} tokens each`,
      },
      {
        key: 'output',
        label: 'Output',
        tokens: planned.outputBudget,
        percentage: planned.percentages.output,
        color: colors.output,
        description: planned.warnings.outputCapHit
          ? `Capped at ${maxOutputTokens.toLocaleString()} (expected ${planned.expectedOutput.toLocaleString()})`
          : `Expected extraction results (${outputPerChunk} tokens/chunk)`,
      },
      {
        key: 'buffer',
        label: 'Buffer',
        tokens: planned.buffer,
        percentage: planned.percentages.buffer,
        color: colors.buffer,
        description: 'Safety buffer for variable content',
      },
    ];

    const showOutputCapLine = maxOutputTokens < planned.availableForOutput;

    // Clamp marker positions so an over-budget usage line pins to the bar's
    // right edge instead of overflowing past it. The true figure is still
    // surfaced via the utilization chips and the over-budget badge.
    const linePositions =
      actualStats && contextWindow > 0
        ? {
            min: clampPercent((actualStats.minUsed / contextWindow) * 100),
            avg: clampPercent((actualStats.avgUsed / contextWindow) * 100),
            max: clampPercent((actualStats.maxUsed / contextWindow) * 100),
          }
        : { min: 0, avg: 0, max: 0 };

    const overBudget = (actualStats?.maxUtilization ?? 0) > 100;

    return {
      planned,
      segments,
      colors,
      contextWindow,
      maxOutputTokens,
      showOutputCapLine,
      linePositions,
      overBudget,
    };
  }, [actualStats, settings, theme]);

  return {
    actualStats,
    ...derived,
  };
}
