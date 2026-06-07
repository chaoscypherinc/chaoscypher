// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactElement, ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { OverviewTab } from '../OverviewTab';
import type { Source, SourceStats } from '../../../../../types';

vi.mock('../hooks/useOverviewData', () => ({ useOverviewData: () => ({ typeToTemplate: {} }) }));
vi.mock('../../pipeline/hooks/useQualityScore', () => ({
  useQualityScore: () => ({ qualityScore: null, qualityLoading: false, recalculateQuality: vi.fn() }),
}));
// PipelineFlowSection has its own test; stub it here so this test stays focused.
vi.mock('../sections/PipelineFlowSection', () => ({ PipelineFlowSection: () => <div data-testid="pipeline-flow" /> }));

function renderTab(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
  return render(ui, { wrapper: Wrapper });
}

const source = {
  id: 's1',
  chunk_count: 12,
  total_content_length: 720000,
  llm_total_calls: 31,
  extraction_entities_count: 5,
  extraction_relationships_count: 2,
  quality_metrics: { cleaner_chars_removed: 5000, vector_indexing_status: 'indexed' },
} as unknown as Source;

describe('OverviewTab', () => {
  it('renders the Pipeline Flow section', () => {
    renderTab(<OverviewTab source={source} stats={null as unknown as SourceStats} />);
    expect(screen.getByTestId('pipeline-flow')).toBeInTheDocument();
  });

  it('clicking the Entities tile calls onNavigateToExtraction', async () => {
    const onExtraction = vi.fn();
    renderTab(
      <OverviewTab source={source} stats={null as unknown as SourceStats} onNavigateToExtraction={onExtraction} />,
    );
    await userEvent.click(screen.getByRole('button', { name: /Entities: .*, open Extraction tab/i }));
    expect(onExtraction).toHaveBeenCalledOnce();
  });

  it('clicking the Quality tile opens the breakdown dialog', async () => {
    renderTab(<OverviewTab source={source} stats={null as unknown as SourceStats} />);
    await userEvent.click(screen.getByRole('button', { name: /Quality|N\/A/i }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
