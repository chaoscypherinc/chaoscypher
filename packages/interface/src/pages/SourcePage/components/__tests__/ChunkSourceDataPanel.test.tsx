// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ChunkSourceDataPanel } from '../ChunkSourceDataPanel';

vi.mock('../../../../services/api/sources', () => ({
  sourcesApi: {
    getChunksByIds: vi.fn(async () => ({
      chunks: [
        { id: 'sc-1', content: 'raw text one', chunk_index: 0 },
        { id: 'sc-2', content: 'raw text two', chunk_index: 1 },
      ],
    })),
  },
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe('ChunkSourceDataPanel', () => {
  it('renders raw chunk text and cleaned LLM input side by side', async () => {
    render(
      wrap(
        <ChunkSourceDataPanel
          sourceId="src-1"
          smallChunkIds={['sc-1', 'sc-2']}
          cleanedInputText="raw text one and two cleaned"
        />,
      ),
    );
    expect(screen.getByText(/raw chunk text/i)).toBeInTheDocument();
    expect(screen.getByText(/cleaned llm input/i)).toBeInTheDocument();
    expect(screen.getByText(/raw text one and two cleaned/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/raw text one/i)).toBeInTheDocument();
    });
  });
});
