// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Pure helpers for context-window math used by ContextBreakdownBar and the
 * context utilization charts.
 *
 * Lives in its own file so the .tsx component file is Fast-Refresh-clean.
 */

/**
 * Default output tokens per chunk (fallback when settings not loaded).
 * Should match the default in core/settings.py ChunkingSettings.output_tokens_per_chunk
 * Conservative estimate for initial pass: avg ~1,500, max ~5,000, using 2,000.
 */
export const DEFAULT_OUTPUT_TOKENS_PER_CHUNK = 2000;

export interface BreakdownResult {
  chunks: number;
  systemTokens: number;
  inputTokens: number;
  /** Expected output before any caps */
  expectedOutput: number;
  /** Actual output budget after applying caps */
  outputBudget: number;
  /** Available space for output (contextWindow - system - input) */
  availableForOutput: number;
  /** Remaining buffer after all allocations */
  buffer: number;
  /** Total used (system + input + outputBudget) */
  totalUsed: number;
  percentages: {
    system: number;
    input: number;
    output: number;
    buffer: number;
    /** Where the output cap falls as % of context window (for indicator line) */
    outputCapPosition: number;
  };
  warnings: {
    /** Output is limited by maxOutputTokens cap */
    outputCapHit: boolean;
    /** Output is limited by available context space */
    contextConstrained: boolean;
    /** High utilization (>80%) */
    highUtilization: boolean;
  };
}

/**
 * Calculate effective overhead based on context window size.
 * Smaller contexts need reduced overhead.
 */
function getEffectiveOverhead(contextWindow: number): number {
  if (contextWindow <= 4096) return 1500;
  if (contextWindow <= 8192) return 2000;
  return 2500;
}

/**
 * Calculate the context breakdown for a given configuration.
 *
 * Key insight: Input + Output must fit within contextWindow (shared pool).
 * Output is additionally capped by maxOutputTokens.
 */
export function calculateContextBreakdown(
  contextWindow: number,
  maxOutputTokens: number,
  inputPerChunk: number,
  outputPerChunk: number = DEFAULT_OUTPUT_TOKENS_PER_CHUNK,
  groupSize: number = 4,
): BreakdownResult {
  const systemTokens = getEffectiveOverhead(contextWindow);
  const chunks = Math.max(1, groupSize);

  const inputTokens = chunks * inputPerChunk;
  const expectedOutput = chunks * outputPerChunk;

  // Available space for output = what's left after system + input
  const availableForOutput = Math.max(0, contextWindow - systemTokens - inputTokens);

  // Output budget is limited by BOTH available space AND maxOutputTokens cap
  const outputBudget = Math.min(expectedOutput, availableForOutput, maxOutputTokens);

  const totalUsed = systemTokens + inputTokens + outputBudget;
  const buffer = Math.max(0, contextWindow - totalUsed);

  // Calculate where the output cap would fall in the bar
  // Position = system + input + maxOutputTokens (clamped to 100%)
  const outputCapPosition = Math.min(
    ((systemTokens + inputTokens + maxOutputTokens) / contextWindow) * 100,
    100,
  );

  // Determine warnings
  const outputCapHit = expectedOutput > maxOutputTokens;
  const contextConstrained =
    expectedOutput > availableForOutput && availableForOutput < maxOutputTokens;
  const utilization = (totalUsed / contextWindow) * 100;
  const highUtilization = utilization > 80;

  return {
    chunks,
    systemTokens,
    inputTokens,
    expectedOutput,
    outputBudget,
    availableForOutput,
    buffer,
    totalUsed,
    percentages: {
      system: (systemTokens / contextWindow) * 100,
      input: (inputTokens / contextWindow) * 100,
      output: (outputBudget / contextWindow) * 100,
      buffer: (buffer / contextWindow) * 100,
      outputCapPosition,
    },
    warnings: {
      outputCapHit,
      contextConstrained,
      highUtilization,
    },
  };
}
