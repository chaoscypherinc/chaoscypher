// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { NotificationProvider } from '../../../../../contexts/NotificationContext';
import { ChunkOverviewBand } from '../ChunkOverviewBand';
import type { ExtractionChartTask, Source } from '../../../../../types';

vi.mock('../../../../../services/api/useVisionPages', () => ({
  useVisionPages: () => ({ data: { job: null } }),
}));

// Stub the recharts-backed charts: noisy in jsdom and unrelated to the
// band's layout. Performance now renders inline (no collapse), so these
// mount eagerly — the stubs keep this test deterministic.
vi.mock('../../charts', () => ({
  ContextUtilizationChart: () => <div data-testid="ctx-chart" />,
  ProcessingTimeChart: () => <div data-testid="time-chart" />,
  EntityDensityChart: () => <div data-testid="density-chart" />,
}));

const chartTasks = [
  { id: 't1', chunk_index: 0, status: 'completed', retry_count: 0, entity_count: 4, relationship_count: 1, invalid_relationship_count: 0, small_chunk_ids: ['c1'] },
] as unknown as ExtractionChartTask[];

const llm = {
  chartTasks,
  stats: null,
  loading: false,
  selectedChunkId: null,
  selectedTask: null,
  selectedTaskLoading: false,
};

const source = { id: 's1', status: 'extracted', chunk_count: 1, quality_metrics: {} } as unknown as Source;

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <NotificationProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </NotificationProvider>
    </QueryClientProvider>
  );
}

describe('ChunkOverviewBand', () => {
  it('renders the chunk overview header and the per-chunk grid', () => {
    render(
      wrap(
        <ChunkOverviewBand source={source} llm={llm} onSelectChunk={vi.fn()} onViewChunk={vi.fn()} />,
      ),
    );
    expect(screen.getByText('Extraction')).toBeInTheDocument();
    expect(screen.getByTestId('chunk-cell-t1')).toBeInTheDocument();
  });

  it('shows the Performance charts inline without expanding a disclosure', () => {
    render(
      wrap(
        <ChunkOverviewBand source={source} llm={llm} onSelectChunk={vi.fn()} onViewChunk={vi.fn()} />,
      ),
    );
    // Performance is no longer collapsed behind a SubSection — the label and
    // the per-chunk charts render immediately.
    expect(screen.getByText('Performance')).toBeInTheDocument();
    expect(screen.getByTestId('time-chart')).toBeInTheDocument();
    expect(screen.getByTestId('density-chart')).toBeInTheDocument();
  });
});
