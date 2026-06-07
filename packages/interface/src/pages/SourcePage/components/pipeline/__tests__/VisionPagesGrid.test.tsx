// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi } from 'vitest';
import { VisionPagesGrid } from '../details/VisionPagesGrid';

vi.mock('../../../../../services/api/useVisionPages', () => ({
  useVisionPages: () => ({
    data: {
      job: { total_pages: 4, completed: 3, failed: 1 },
      pages: [
        { id: 'v1', page_number: 1, region_index: 0, status: 'succeeded', error_message: null, description: 'ok' },
        { id: 'v2', page_number: 2, region_index: 0, status: 'succeeded', error_message: null, description: 'ok' },
        { id: 'v3', page_number: 3, region_index: 0, status: 'failed', error_message: 'vision empty', description: null },
        { id: 'v4', page_number: 4, region_index: 0, status: 'succeeded', error_message: null, description: 'ok' },
      ],
    },
    isLoading: false,
    error: null,
  }),
  useRetryVisionPage: () => ({ mutate: vi.fn(), isPending: false }),
  useRetryFailedVisionPages: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../../../../services/api/useSourceImages', () => ({
  useSourceImages: () => ({
    data: [
      { filename: 'page_3.png', url: 'http://x/page_3.png' },
    ],
  }),
  pageNumberFromFilename: (s: string) => {
    const match = s.match(/^page_(\d+)\.png$/i);
    return match ? parseInt(match[1], 10) : null;
  },
}));

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe('VisionPagesGrid', () => {
  it('renders a cell per page', () => {
    render(wrap(<VisionPagesGrid sourceId="s1" sourceStatus="vision_pending" />));
    expect(screen.getByTestId('vision-cell-v1')).toBeInTheDocument();
    expect(screen.getByTestId('vision-cell-v4')).toBeInTheDocument();
  });

  it('shows "Retry N failed" button when pre-finalize and >0 failures', () => {
    render(wrap(<VisionPagesGrid sourceId="s1" sourceStatus="vision_pending" />));
    expect(screen.getByRole('button', { name: /retry 1 failed/i })).toBeInTheDocument();
  });

  it('hides the retry-batch button when source has advanced past vision_pending', () => {
    render(wrap(<VisionPagesGrid sourceId="s1" sourceStatus="indexed" />));
    expect(screen.queryByRole('button', { name: /retry.*failed/i })).toBeNull();
  });

  it('clicking a cell shows detail panel with status + error', async () => {
    render(wrap(<VisionPagesGrid sourceId="s1" sourceStatus="vision_pending" />));
    await userEvent.click(screen.getByTestId('vision-cell-v3'));
    expect(screen.getByTestId('vision-detail-panel')).toBeInTheDocument();
    expect(screen.getByText(/vision empty/)).toBeInTheDocument();
  });

  it('renders the page thumbnail when a matching image url is available', async () => {
    render(wrap(<VisionPagesGrid sourceId="s1" sourceStatus="vision_pending" />));
    await userEvent.click(screen.getByTestId('vision-cell-v3'));
    const img = screen.getByAltText(/page 3/i) as HTMLImageElement;
    expect(img).toBeInTheDocument();
    expect(img.src).toContain('/page_3.png');
    expect(screen.queryByText(/Page image not available/i)).toBeNull();
  });
});
