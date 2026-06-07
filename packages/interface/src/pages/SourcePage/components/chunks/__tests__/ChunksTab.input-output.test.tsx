// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { describe, it, expect, vi } from 'vitest';
import type { ReactElement } from 'react';
import { ChunksTab } from '../../ChunksTab';
import type { Source } from '../../../../../types';

vi.mock('../ChunkOverviewBand', () => ({ ChunkOverviewBand: () => null }));
vi.mock('../../pipeline/PromptsSection', () => ({ PromptsSection: () => null }));
vi.mock('../../pipeline/hooks/useLLMProcessing', () => ({
  useLLMProcessing: () => ({ state: { chartTasks: [], stats: null, loading: false, selectedChunkId: null, selectedTask: null, selectedTaskLoading: false }, selectChunk: vi.fn() }),
}));

// Mock the chunk list endpoint + tab-level OUTPUT feeds so the tab can
// render without a live backend. The shapes here match the real
// ``sourcesApi`` surface (see services/api/sourceProcessing.ts).
vi.mock('../../../../../services/api/sources', () => ({
  sourcesApi: {
    getChunks: vi.fn(async () => ({
      data: [
        {
          id: 'c1',
          chunk_index: 0,
          content: 'Cleaned text.',
          source_id: 's1',
          status: 'committed',
          created_at: '',
        },
      ],
      pagination: {
        total: 1,
        page: 1,
        page_size: 50,
        total_pages: 1,
        has_next: false,
        has_prev: false,
      },
    })),
    getEntities: vi.fn(async () => ({
      entities: [],
      pagination: {
        total: 0,
        page: 1,
        page_size: 1000,
        total_pages: 1,
        has_next: false,
        has_prev: false,
      },
    })),
    getRelationships: vi.fn(async () => ({
      relationships: [],
      pagination: {
        total: 0,
        page: 1,
        page_size: 1000,
        total_pages: 1,
        has_next: false,
        has_prev: false,
      },
    })),
    getExtractionTasks: vi.fn(async () => ({
      tasks: [],
      total: 0,
      page: 1,
      page_size: 1000,
    })),
  },
}));

// Mock the heavy per-chunk detail fetch — each MemberChunkRow calls this
// when it mounts so the INPUT view can show the [Show removed text] overlay.
vi.mock('../../../../../services/api/useChunkDetail', () => ({
  useChunkDetail: (_sourceId: string, chunkId: string | null) =>
    chunkId
      ? {
          data: {
            id: chunkId,
            chunk_index: 0,
            content: 'Cleaned text.',
            raw_content: 'Raw cleaned text.',
          },
          isLoading: false,
        }
      : { data: undefined, isLoading: false },
}));

// useSourceImages + useVisionPages are unrelated to the INPUT/OUTPUT
// surface; mock them as empty so the tab doesn't try to hit the
// thumbnail / vision endpoints during the test.
vi.mock('../../../../../services/api/useSourceImages', () => ({
  useSourceImages: () => ({ data: [] }),
  pageNumberFromFilename: () => null,
}));
vi.mock('../../../../../services/api/useVisionPages', () => ({
  useVisionPages: () => ({ data: { pages: [], job: null } }),
}));

function wrap(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe('ChunksTab — INPUT/OUTPUT integration (Separate mode)', () => {
  it('defaults to INPUT view per group and shows the cleaned content', async () => {
    render(wrap(<ChunksTab source={{ id: 's1', status: 'indexed', upload_options: { enable_vision: false } } as unknown as Source} />));
    // The INPUT toggle is rendered by ChunkContentToggle inside
    // GroupExtractionBody — wait for the group body to mount.
    expect(await screen.findByText('INPUT')).toBeInTheDocument();
    // INPUT view renders the cleaned markdown body.
    expect(await screen.findByText('Cleaned text.')).toBeInTheDocument();
  });

  it('toggling tab-level "Show removed text" switch flips to the diff overlay', async () => {
    render(wrap(<ChunksTab source={{ id: 's1', status: 'indexed', upload_options: { enable_vision: false } } as unknown as Source} />));
    await screen.findByText('Cleaned text.');
    // MUI Switch reports role="switch" (not "checkbox"); accessible
    // name comes from the FormControlLabel text.
    await userEvent.click(
      screen.getByRole('switch', { name: /show removed text/i }),
    );
    expect(screen.getByTestId('chunk-input-diff')).toBeInTheDocument();
  });
});
