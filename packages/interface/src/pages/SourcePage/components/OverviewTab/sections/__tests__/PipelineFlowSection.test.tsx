// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PipelineFlowSection } from '../PipelineFlowSection';
import type { Source, SourceStats } from '../../../../../../types';

vi.mock('../../../pipeline/hooks/useLLMProcessing', () => ({
  useLLMProcessing: () => ({ state: { stats: null }, selectChunk: vi.fn() }),
}));
vi.mock('../../../../../../services/api/useVisionPages', () => ({
  useVisionPages: () => ({ data: { job: null } }),
}));

const source = {
  id: 's1',
  status: 'committed',
  chunk_count: 12,
  total_content_length: 720000,
  llm_total_calls: 31,
  quality_metrics: { cleaner_chars_removed: 5000, vector_indexing_status: 'indexed' },
} as unknown as Source;

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe('PipelineFlowSection', () => {
  it('shows the stat-card strip collapsed and the funnel after expanding', async () => {
    render(wrap(<PipelineFlowSection source={source} stats={null as unknown as SourceStats} />));
    // Collapsed: cards visible, funnel pills not.
    expect(screen.getByText('Loaded')).toBeInTheDocument();
    expect(screen.queryByText('EXTRACT')).toBeNull();
    // Expand.
    await userEvent.click(screen.getByRole('button', { name: /pipeline flow/i }));
    expect(screen.getAllByText('LOAD').length).toBeGreaterThanOrEqual(1);
  });
});
