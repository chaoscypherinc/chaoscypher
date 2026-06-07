// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for ContextUtilizationChart's over-budget treatment.
 *
 * Renders the real component over the real useContextUtilization hook (with
 * settingsApi + calculateContextBreakdown mocked, mirroring the hook test) so
 * the over-100% path is exercised end to end: the resting bar stays clean
 * (just the avg %), and an "Over budget" badge appears only when peak usage
 * exceeded the window.
 */

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

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

vi.mock('../../../../../services/api/settings', () => ({
  settingsApi: { get: mockSettingsGet },
}));

vi.mock('../../../../../components', () => ({
  calculateContextBreakdown: mockCalculateContextBreakdown,
}));

vi.mock('../../../../../utils/logger', () => ({
  logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
}));

import { ContextUtilizationChart } from '../ContextUtilizationChart';
import type { ExtractionTaskStats } from '../../../../../types';
import type { BreakdownResult } from '../../../../../components';

function makeBreakdown(): BreakdownResult {
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
    warnings: { outputCapHit: false, contextConstrained: false, highUtilization: false },
  };
}

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
    context_window: 10000,
    min_total_tokens: 5000,
    max_total_tokens: 8000,
    avg_total_tokens: 6500,
    min_input_tokens: 4000,
    max_input_tokens: 6000,
    avg_input_tokens: 5000,
    min_output_tokens: 1000,
    max_output_tokens: 2000,
    avg_output_tokens: 1500,
    min_utilization: 50.0,
    max_utilization: 80.0,
    avg_utilization: 65.0,
    ...overrides,
  } as ExtractionTaskStats;
}

function renderChart(stats: ExtractionTaskStats) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<ContextUtilizationChart tasks={[]} stats={stats} />, { wrapper });
}

describe('ContextUtilizationChart over-budget badge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCalculateContextBreakdown.mockReturnValue(makeBreakdown());
    mockSettingsGet.mockResolvedValue(null);
  });

  it('shows the "Over budget" badge when peak utilization exceeds 100%', async () => {
    // 15000 / 10000 = 150% peak usage. The high marker carries the true peak
    // on the line (avg 65% appears twice — headline + mean marker — so we
    // assert on the unique high value).
    renderChart(makeStats({ max_total_tokens: 15000, max_utilization: 150.0 }));

    expect(await screen.findByText('Over budget')).toBeInTheDocument();
    expect(screen.getByText('150%')).toBeInTheDocument();
  });

  it('hides the "Over budget" badge when usage stays within the window', async () => {
    renderChart(makeStats());

    // High marker shows the peak (80%); no over-budget badge within the window.
    expect(await screen.findByText('80%')).toBeInTheDocument();
    expect(screen.queryByText('Over budget')).toBeNull();
  });
});
