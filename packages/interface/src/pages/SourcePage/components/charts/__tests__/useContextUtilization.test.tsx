// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useContextUtilization hook and its pure helper exports.
 *
 * Pure helpers (getUtilizationColor, formatNumber) are tested exhaustively
 * since they provide an easy, high-confidence coverage chunk.
 *
 * The hook is tested by mocking settingsApi and calculateContextBreakdown
 * while leaving @mui/material and ContextColors unmocked (they have no
 * network side-effects and provide real values).
 */

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ---------------------------------------------------------------------------
// Hoisted mock handles — vi.hoisted ensures these are available before the
// vi.mock factories execute (both are hoisted to the top of the file by Vitest).
// ---------------------------------------------------------------------------

const { mockSettingsGet, mockCalculateContextBreakdown } = vi.hoisted(() => ({
  mockSettingsGet: vi.fn<() => Promise<unknown>>(),
  mockCalculateContextBreakdown: vi.fn<
    (
      contextWindow: number,
      maxOutputTokens: number,
      inputPerChunk: number,
      outputPerChunk: number,
      groupSize: number,
    ) => import('../../../../../components').BreakdownResult
  >(),
}));

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../../../../../services/api/settings', () => ({
  settingsApi: {
    get: mockSettingsGet,
  },
}));

vi.mock('../../../../../components', () => ({
  calculateContextBreakdown: mockCalculateContextBreakdown,
}));

vi.mock('../../../../../utils/logger', () => ({
  logger: {
    error: vi.fn<(...args: unknown[]) => void>(),
    warn: vi.fn<(...args: unknown[]) => void>(),
    info: vi.fn<(...args: unknown[]) => void>(),
    debug: vi.fn<(...args: unknown[]) => void>(),
  },
}));

// ---------------------------------------------------------------------------
// Module under test (imported AFTER mocks are hoisted)
// ---------------------------------------------------------------------------

import {
  getUtilizationColor,
  formatNumber,
  useContextUtilization,
} from '../useContextUtilization';
import type { ExtractionTask, ExtractionTaskStats } from '../../../../../types';
import type { BreakdownResult } from '../../../../../components';

// ---------------------------------------------------------------------------
// Shared test fixtures
// ---------------------------------------------------------------------------

/** A minimal BreakdownResult that satisfies the interface. */
function makeBreakdown(overrides: Partial<BreakdownResult> = {}): BreakdownResult {
  return {
    chunks: 4,
    systemTokens: 2500,
    inputTokens: 600,
    expectedOutput: 8000,
    outputBudget: 4096,
    availableForOutput: 12900,
    buffer: 900,
    totalUsed: 7196,
    percentages: {
      system: 15.6,
      input: 3.75,
      output: 25.6,
      buffer: 5.6,
      outputCapPosition: 44.9,
    },
    warnings: {
      outputCapHit: false,
      contextConstrained: false,
      highUtilization: false,
    },
    ...overrides,
  };
}

/** Minimal ExtractionTask with token data. */
function makeTask(
  input: number,
  output: number,
  contextWindow: number,
): ExtractionTask {
  return {
    id: `task-${Math.random()}`,
    job_id: 'job-1',
    chunk_index: 0,
    status: 'completed',
    retry_count: 0,
    entity_count: 0,
    relationship_count: 0,
    invalid_relationship_count: 0,
    created_at: new Date().toISOString(),
    input_tokens: input,
    output_tokens: output,
    context_window_available: contextWindow,
  };
}

/** Full ExtractionTaskStats that passes the null-guard in statsToActualStats. */
function makeStats(overrides: Partial<ExtractionTaskStats> = {}): ExtractionTaskStats {
  return {
    total_tasks: 10,
    total_entities: 100,
    avg_entities_per_task: 10,
    total_relationships: 50,
    avg_relationships_per_task: 5,
    total_retries: 0,
    max_retries_single_task: 0,
    total_invalid_relationships: 0,
    avg_invalid_per_task: 0,
    total_entities_filtered: 0,
    total_relationships_filtered: 0,
    context_window: 16000,
    min_total_tokens: 1000,
    max_total_tokens: 8000,
    avg_total_tokens: 4000,
    min_input_tokens: 800,
    max_input_tokens: 6000,
    avg_input_tokens: 3000,
    min_output_tokens: 200,
    max_output_tokens: 2000,
    avg_output_tokens: 1000,
    min_utilization: 6.25,
    max_utilization: 50.0,
    avg_utilization: 25.0,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Render helper — wraps the hook in a fresh QueryClientProvider so the
// migrated useQuery has its required context. Retries off + a fresh client
// per render keeps tests isolated (no cross-test cache bleed).
// ---------------------------------------------------------------------------

function renderContextUtil(
  tasks: ExtractionTask[],
  stats: ExtractionTaskStats | null | undefined,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return renderHook(() => useContextUtilization(tasks, stats), { wrapper });
}

// ---------------------------------------------------------------------------
// Pure helper: getUtilizationColor
// ---------------------------------------------------------------------------

describe('getUtilizationColor', () => {
  describe('returns "success" for utilization at or below 75%', () => {
    it('returns success for 0%', () => {
      expect(getUtilizationColor(0)).toBe('success');
    });

    it('returns success for 50%', () => {
      expect(getUtilizationColor(50)).toBe('success');
    });

    it('returns success for exactly 75%', () => {
      // Boundary: util > 75 triggers warning, so 75 itself is still success
      expect(getUtilizationColor(75)).toBe('success');
    });

    it('returns success for 74.9%', () => {
      expect(getUtilizationColor(74.9)).toBe('success');
    });
  });

  describe('returns "warning" for utilization above 75% and at or below 90%', () => {
    it('returns warning for 75.1%', () => {
      expect(getUtilizationColor(75.1)).toBe('warning');
    });

    it('returns warning for 80%', () => {
      expect(getUtilizationColor(80)).toBe('warning');
    });

    it('returns warning for exactly 90%', () => {
      // Boundary: util > 90 triggers error, so 90 itself is still warning
      expect(getUtilizationColor(90)).toBe('warning');
    });

    it('returns warning for 89.99%', () => {
      expect(getUtilizationColor(89.99)).toBe('warning');
    });
  });

  describe('returns "error" for utilization above 90%', () => {
    it('returns error for 90.01%', () => {
      expect(getUtilizationColor(90.01)).toBe('error');
    });

    it('returns error for 95%', () => {
      expect(getUtilizationColor(95)).toBe('error');
    });

    it('returns error for 100%', () => {
      expect(getUtilizationColor(100)).toBe('error');
    });

    it('returns error for values above 100%', () => {
      expect(getUtilizationColor(120)).toBe('error');
    });
  });
});

// ---------------------------------------------------------------------------
// Pure helper: formatNumber
// ---------------------------------------------------------------------------

describe('formatNumber', () => {
  it('formats 0', () => {
    expect(formatNumber(0)).toBe('0');
  });

  it('formats small numbers without separators', () => {
    expect(formatNumber(42)).toBe('42');
    expect(formatNumber(999)).toBe('999');
  });

  it('formats thousands with locale separator', () => {
    const result = formatNumber(1000);
    // toLocaleString() uses comma in most locales; accept any non-digit separator
    expect(result).toMatch(/1.000|1,000/);
  });

  it('formats large numbers (millions)', () => {
    const result = formatNumber(1_000_000);
    // Should contain at least two separator instances
    expect(result.length).toBeGreaterThan(6);
  });

  it('is consistent with native toLocaleString', () => {
    const n = 12345;
    expect(formatNumber(n)).toBe(n.toLocaleString());
  });
});

// ---------------------------------------------------------------------------
// Hook: useContextUtilization
// ---------------------------------------------------------------------------

describe('useContextUtilization', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCalculateContextBreakdown.mockReturnValue(makeBreakdown());
    mockSettingsGet.mockResolvedValue(null);
  });

  // -------------------------------------------------------------------------
  // Segment-building from backend stats
  // -------------------------------------------------------------------------

  it('uses statsToActualStats when stats prop is provided and has required fields', async () => {
    const stats = makeStats();
    const { result } = renderContextUtil([], stats);

    // actualStats should be non-null
    await waitFor(() => {
      expect(result.current.actualStats).not.toBeNull();
    });

    expect(result.current.actualStats?.minUsed).toBe(stats.min_total_tokens);
    expect(result.current.actualStats?.maxUsed).toBe(stats.max_total_tokens);
    expect(result.current.actualStats?.avgUsed).toBe(stats.avg_total_tokens);
    expect(result.current.actualStats?.contextWindow).toBe(stats.context_window);
    expect(result.current.actualStats?.tasksWithData).toBe(stats.total_tasks);
  });

  it('falls back to task-based calculation when stats is null', async () => {
    const tasks = [makeTask(1000, 500, 16000), makeTask(2000, 800, 16000)];
    const { result } = renderContextUtil(tasks, null);

    await waitFor(() => {
      expect(result.current.actualStats).not.toBeNull();
    });

    expect(result.current.actualStats?.tasksWithData).toBe(2);
    expect(result.current.actualStats?.minUsed).toBe(1500); // 1000+500
    expect(result.current.actualStats?.maxUsed).toBe(2800); // 2000+800
    expect(result.current.actualStats?.contextWindow).toBe(16000);
  });

  it('returns null actualStats when tasks have no token data', async () => {
    const taskNoTokens: ExtractionTask = {
      id: 'x',
      job_id: 'j',
      chunk_index: 0,
      status: 'pending',
      retry_count: 0,
      entity_count: 0,
      relationship_count: 0,
      invalid_relationship_count: 0,
      created_at: new Date().toISOString(),
      // input_tokens / output_tokens / context_window_available all undefined
    };
    const { result } = renderContextUtil([taskNoTokens], null);

    await waitFor(() => {
      // actualStats should remain null as there's no token data
      expect(result.current.actualStats).toBeNull();
    });
  });

  it('returns null actualStats when both tasks and stats are empty/null', async () => {
    const { result } = renderContextUtil([], null);

    await waitFor(() => {
      expect(result.current.actualStats).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // Segment structure
  // -------------------------------------------------------------------------

  it('returns four segments with expected keys', async () => {
    const breakdown = makeBreakdown();
    mockCalculateContextBreakdown.mockReturnValue(breakdown);

    const { result } = renderContextUtil([], null);

    await waitFor(() => {
      expect(result.current.segments).toHaveLength(4);
    });

    const keys = result.current.segments.map((s) => s.key);
    expect(keys).toEqual(['system', 'input', 'output', 'buffer']);
  });

  it('populates segment tokens from the breakdown result', async () => {
    const breakdown = makeBreakdown({
      systemTokens: 2500,
      inputTokens: 600,
      outputBudget: 4096,
      buffer: 900,
    });
    mockCalculateContextBreakdown.mockReturnValue(breakdown);

    const { result } = renderContextUtil([], null);

    await waitFor(() => {
      expect(result.current.segments[0].tokens).toBe(2500);
    });

    expect(result.current.segments[1].tokens).toBe(600);
    expect(result.current.segments[2].tokens).toBe(4096);
    expect(result.current.segments[3].tokens).toBe(900);
  });

  it('populates segment percentages from the breakdown percentages', async () => {
    const breakdown = makeBreakdown();
    mockCalculateContextBreakdown.mockReturnValue(breakdown);

    const { result } = renderContextUtil([], null);

    await waitFor(() => {
      expect(result.current.segments[0].percentage).toBe(breakdown.percentages.system);
    });
    expect(result.current.segments[1].percentage).toBe(breakdown.percentages.input);
    expect(result.current.segments[2].percentage).toBe(breakdown.percentages.output);
    expect(result.current.segments[3].percentage).toBe(breakdown.percentages.buffer);
  });

  // -------------------------------------------------------------------------
  // Colors palette
  // -------------------------------------------------------------------------

  it('returns a colors object with system, input, output, outputCap, buffer keys', async () => {
    const { result } = renderContextUtil([], null);

    await waitFor(() => {
      expect(result.current.colors).toBeDefined();
    });

    expect(typeof result.current.colors.system).toBe('string');
    expect(typeof result.current.colors.input).toBe('string');
    expect(typeof result.current.colors.output).toBe('string');
    expect(typeof result.current.colors.outputCap).toBe('string');
    expect(typeof result.current.colors.buffer).toBe('string');
  });

  // -------------------------------------------------------------------------
  // Line positions
  // -------------------------------------------------------------------------

  it('computes linePositions as zeros when actualStats is null', async () => {
    const { result } = renderContextUtil([], null);

    await waitFor(() => {
      expect(result.current.linePositions).toEqual({ min: 0, avg: 0, max: 0 });
    });
  });

  it('computes linePositions as percentages when actualStats is available', async () => {
    const stats = makeStats({
      context_window: 10000,
      min_total_tokens: 1000,
      avg_total_tokens: 5000,
      max_total_tokens: 8000,
    });

    const { result } = renderContextUtil([], stats);

    await waitFor(() => {
      expect(result.current.actualStats).not.toBeNull();
    });

    expect(result.current.linePositions.min).toBeCloseTo(10);  // 1000/10000*100
    expect(result.current.linePositions.avg).toBeCloseTo(50);  // 5000/10000*100
    expect(result.current.linePositions.max).toBeCloseTo(80);  // 8000/10000*100
  });

  // -------------------------------------------------------------------------
  // Context window and max output tokens
  // -------------------------------------------------------------------------

  it('derives contextWindow from actualStats when no settings', async () => {
    const stats = makeStats({ context_window: 32000 });
    // settings returns null → no llm.ai_context_window
    mockSettingsGet.mockResolvedValue(null);

    const { result } = renderContextUtil([], stats);

    await waitFor(() => {
      expect(result.current.contextWindow).toBe(32000);
    });
  });

  it('showOutputCapLine is true when maxOutputTokens < availableForOutput', async () => {
    const breakdown = makeBreakdown({
      availableForOutput: 12000,
    });
    mockCalculateContextBreakdown.mockReturnValue(breakdown);

    const stats = makeStats({ context_window: 16000 });
    mockSettingsGet.mockResolvedValue(null);

    const { result } = renderContextUtil([], stats);

    await waitFor(() => {
      // maxOutputTokens = floor(contextWindow * 0.25) = floor(16000 * 0.25) = 4000
      // availableForOutput = 12000, so 4000 < 12000 → showOutputCapLine true
      expect(result.current.showOutputCapLine).toBe(true);
    });
  });

  it('showOutputCapLine is false when maxOutputTokens >= availableForOutput', async () => {
    const breakdown = makeBreakdown({
      availableForOutput: 1000,
    });
    mockCalculateContextBreakdown.mockReturnValue(breakdown);

    const stats = makeStats({ context_window: 16000 });
    mockSettingsGet.mockResolvedValue(null);

    const { result } = renderContextUtil([], stats);

    await waitFor(() => {
      // maxOutputTokens = 4000, availableForOutput = 1000 → 4000 >= 1000 → false
      expect(result.current.showOutputCapLine).toBe(false);
    });
  });

  // -------------------------------------------------------------------------
  // Settings fetch: success path
  // -------------------------------------------------------------------------

  it('uses settings llm.ai_context_window when available', async () => {
    const fakeSettings = {
      llm: {
        ai_context_window: 128000,
        extraction_max_tokens: 8000,
      },
      chunking: {
        output_tokens_per_chunk: 3000,
        group_size: 5,
        small_chunk_size: 2400,
      },
    };
    mockSettingsGet.mockResolvedValue(fakeSettings);

    const { result } = renderContextUtil([], null);

    await waitFor(() => {
      expect(result.current.contextWindow).toBe(128000);
    });

    expect(result.current.maxOutputTokens).toBe(8000);
  });

  it('passes settings-derived params to calculateContextBreakdown', async () => {
    const fakeSettings = {
      llm: {
        ai_context_window: 16000,
        extraction_max_tokens: 4096,
      },
      chunking: {
        output_tokens_per_chunk: 2500,
        group_size: 6,
        small_chunk_size: 800,
      },
    };
    mockSettingsGet.mockResolvedValue(fakeSettings);

    const { result } = renderContextUtil([], null);

    await waitFor(() => {
      expect(result.current.contextWindow).toBe(16000);
    });

    expect(mockCalculateContextBreakdown).toHaveBeenCalledWith(
      16000,        // contextWindow from settings
      4096,         // extraction_max_tokens
      200,          // floor(800 / 4) = 200
      2500,         // output_tokens_per_chunk
      6,            // group_size
    );
  });

  // -------------------------------------------------------------------------
  // Settings fetch: error path
  // -------------------------------------------------------------------------

  it('calls logger.error when settingsApi.get rejects', async () => {
    const fetchError = new Error('Network failure');
    mockSettingsGet.mockRejectedValue(fetchError);

    const { logger } = await import('../../../../../utils/logger');

    const { result } = renderContextUtil([], null);

    await waitFor(() => {
      expect(logger.error).toHaveBeenCalledWith(fetchError);
    });

    // Hook should still return a valid (non-crashing) result
    expect(result.current.segments).toHaveLength(4);
  });

  // -------------------------------------------------------------------------
  // stats null-guard: statsToActualStats returns null for missing required fields
  // -------------------------------------------------------------------------

  it('returns null actualStats when stats is missing min_total_tokens', async () => {
    const incompleteStats = makeStats({ min_total_tokens: undefined });

    const { result } = renderContextUtil([], incompleteStats);

    await waitFor(() => {
      // The fallback from calculateActualStatsFromTasks([]=[]) is also null
      expect(result.current.actualStats).toBeNull();
    });
  });

  it('returns null actualStats when stats is missing context_window', async () => {
    const incompleteStats = makeStats({ context_window: undefined });

    const { result } = renderContextUtil([], incompleteStats);

    await waitFor(() => {
      expect(result.current.actualStats).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // Output cap description in segment
  // -------------------------------------------------------------------------

  it('output segment description mentions cap when outputCapHit warning is set', async () => {
    const breakdown = makeBreakdown({
      warnings: { outputCapHit: true, contextConstrained: false, highUtilization: false },
      expectedOutput: 12000,
    });
    mockCalculateContextBreakdown.mockReturnValue(breakdown);

    const stats = makeStats({ context_window: 16000 });
    mockSettingsGet.mockResolvedValue(null);

    const { result } = renderContextUtil([], stats);

    await waitFor(() => {
      const outputSegment = result.current.segments.find((s) => s.key === 'output');
      expect(outputSegment?.description).toMatch(/[Cc]apped/);
    });
  });

  it('output segment description shows per-chunk note when no cap hit', async () => {
    const breakdown = makeBreakdown({
      warnings: { outputCapHit: false, contextConstrained: false, highUtilization: false },
    });
    mockCalculateContextBreakdown.mockReturnValue(breakdown);

    const { result } = renderContextUtil([], null);

    await waitFor(() => {
      const outputSegment = result.current.segments.find((s) => s.key === 'output');
      expect(outputSegment?.description).toMatch(/tokens\/chunk/);
    });
  });

  // -------------------------------------------------------------------------
  // Task-based calculation: edge cases
  // -------------------------------------------------------------------------

  it('computes correct avg from multiple tasks', async () => {
    const tasks = [
      makeTask(1000, 500, 8000),  // total 1500
      makeTask(3000, 1000, 8000), // total 4000
    ];
    const { result } = renderContextUtil(tasks, null);

    await waitFor(() => {
      expect(result.current.actualStats).not.toBeNull();
    });

    // avgUsed = round((1500 + 4000) / 2) = round(2750) = 2750
    expect(result.current.actualStats?.avgUsed).toBe(2750);
    expect(result.current.actualStats?.minUsed).toBe(1500);
    expect(result.current.actualStats?.maxUsed).toBe(4000);
  });

  it('computes utilization percentages from task data', async () => {
    const tasks = [makeTask(4000, 4000, 16000)]; // total 8000 of 16000 = 50%
    const { result } = renderContextUtil(tasks, null);

    await waitFor(() => {
      expect(result.current.actualStats).not.toBeNull();
    });

    expect(result.current.actualStats?.avgUtilization).toBeCloseTo(50);
    expect(result.current.actualStats?.minUtilization).toBeCloseTo(50);
    expect(result.current.actualStats?.maxUtilization).toBeCloseTo(50);
  });

  // -------------------------------------------------------------------------
  // planned field is always returned
  // -------------------------------------------------------------------------

  it('always returns the planned field from calculateContextBreakdown', async () => {
    const breakdown = makeBreakdown();
    mockCalculateContextBreakdown.mockReturnValue(breakdown);

    const { result } = renderContextUtil([], null);

    await waitFor(() => {
      expect(result.current.planned).toBeDefined();
    });

    expect(result.current.planned.chunks).toBe(breakdown.chunks);
    expect(result.current.planned.systemTokens).toBe(breakdown.systemTokens);
  });

  // -------------------------------------------------------------------------
  // Context-window precedence: stored extraction-time window wins over live
  // settings so the bar's markers share the backend chips' denominator.
  // -------------------------------------------------------------------------

  it('prefers the stored extraction-time window over the live setting', async () => {
    const stats = makeStats({ context_window: 10000 });
    mockSettingsGet.mockResolvedValue({
      llm: { ai_context_window: 128000, extraction_max_tokens: 4096 },
      chunking: { output_tokens_per_chunk: 2000, group_size: 4, small_chunk_size: 600 },
    });

    const { result } = renderContextUtil([], stats);

    // maxOutputTokens only reaches 4096 once settings resolve (the pre-settings
    // default is floor(10000 * 0.25) = 2500), so this also proves the assertion
    // runs after the live 128000 window is available to lose to the stored one.
    await waitFor(() => {
      expect(result.current.maxOutputTokens).toBe(4096);
    });

    // Stored 10000 wins over the live 128000 setting.
    expect(result.current.contextWindow).toBe(10000);
    expect(mockCalculateContextBreakdown).toHaveBeenLastCalledWith(
      10000,
      4096,
      150, // floor(600 / 4)
      2000,
      4,
    );
  });

  // -------------------------------------------------------------------------
  // Over-budget: usage beyond the window clamps marker lines and flags it.
  // -------------------------------------------------------------------------

  it('clamps marker positions to 100 and sets overBudget when usage exceeds the window', async () => {
    const stats = makeStats({
      context_window: 10000,
      min_total_tokens: 5000, // 50%
      avg_total_tokens: 8000, // 80%
      max_total_tokens: 15000, // 150% → clamps
      max_utilization: 150.0,
    });

    const { result } = renderContextUtil([], stats);

    await waitFor(() => {
      expect(result.current.actualStats).not.toBeNull();
    });

    expect(result.current.linePositions.min).toBeCloseTo(50);
    expect(result.current.linePositions.avg).toBeCloseTo(80);
    expect(result.current.linePositions.max).toBe(100); // clamped from 150
    expect(result.current.overBudget).toBe(true);
  });

  it('leaves overBudget false when peak usage is within the window', async () => {
    const stats = makeStats({ max_utilization: 50.0 });

    const { result } = renderContextUtil([], stats);

    await waitFor(() => {
      expect(result.current.actualStats).not.toBeNull();
    });

    expect(result.current.overBudget).toBe(false);
  });
});
