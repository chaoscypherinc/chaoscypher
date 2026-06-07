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

// A two-chunk group (group_index 0, chunks c1 + c2) produced by ONE
// extraction task. The task and its entities carry the group ordinal (0) as
// their chunk_index — this is the per-group contract the tab now relies on.
vi.mock('../../../../../services/api/sources', () => ({
  sourcesApi: {
    getChunks: vi.fn(async () => ({
      data: [
        {
          id: 'c1',
          chunk_index: 0,
          group_index: 0,
          content: 'First chunk text.',
          source_id: 's1',
          status: 'committed',
          created_at: '',
        },
        {
          id: 'c2',
          chunk_index: 1,
          group_index: 0,
          content: 'Second chunk text.',
          source_id: 's1',
          status: 'committed',
          created_at: '',
        },
      ],
      pagination: {
        total: 2,
        page: 1,
        page_size: 50,
        total_pages: 1,
        has_next: false,
        has_prev: false,
      },
    })),
    getEntities: vi.fn(async () => ({
      // chunk_index 0 == the group ordinal (set per group in ai_entities.py).
      entities: [{ name: 'Acme Corp', type: 'org', chunk_index: 0, confidence: 0.91 }],
      pagination: {
        total: 1,
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
      tasks: [
        {
          id: 't1',
          job_id: 'j1',
          chunk_index: 0,
          small_chunk_ids: ['c1', 'c2'],
          status: 'completed',
          created_at: '',
          retry_count: 0,
          entity_count: 1,
          relationship_count: 0,
          invalid_relationship_count: 0,
          input_tokens: 1234,
          output_tokens: 567,
          llm_duration_ms: 3200,
        },
      ],
      total: 1,
      page: 1,
      page_size: 1000,
    })),
  },
}));

vi.mock('../../../../../services/api/useChunkDetail', () => ({
  useChunkDetail: (_sourceId: string, chunkId: string | null) =>
    chunkId
      ? { data: { id: chunkId, raw_content: null }, isLoading: false }
      : { data: undefined, isLoading: false },
}));

vi.mock('../../../../../services/api/useSourceImages', () => ({
  useSourceImages: () => ({ data: [] }),
  pageNumberFromFilename: () => null,
}));
vi.mock('../../../../../services/api/useVisionPages', () => ({
  useVisionPages: () => ({ data: { pages: [], job: null } }),
}));

function wrap(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe('ChunksTab — per-group INPUT/OUTPUT (Separate mode)', () => {
  it('renders ONE toggle for a multi-chunk group and shows both chunk texts', async () => {
    render(wrap(<ChunksTab source={{ id: 's1', status: 'indexed', upload_options: { enable_vision: false } } as unknown as Source} />));
    expect(await screen.findByText('First chunk text.')).toBeInTheDocument();
    expect(screen.getByText('Second chunk text.')).toBeInTheDocument();
    // A single group → a single INPUT/OUTPUT toggle (getByRole throws if >1).
    expect(screen.getByRole('button', { name: 'OUTPUT' })).toBeInTheDocument();
  });

  it('OUTPUT shows the group entities, token usage, and the "Show filtered" switch — while INPUT text stays visible', async () => {
    render(wrap(<ChunksTab source={{ id: 's1', status: 'indexed', upload_options: { enable_vision: false } } as unknown as Source} />));
    await screen.findByText('First chunk text.');

    await userEvent.click(screen.getByRole('button', { name: 'OUTPUT' }));

    // Group-level extraction output for the whole group.
    expect(screen.getByText(/ENTITIES KEPT \(1\)/)).toBeInTheDocument();
    expect(screen.getByText('Acme Corp')).toBeInTheDocument();
    // Token usage surfaced from the matched task.
    expect(screen.getByText(/Tokens:\s*1,234 in · 567 out/)).toBeInTheDocument();
    // "Show filtered" control appears in OUTPUT mode.
    expect(screen.getByRole('switch', { name: /show filtered/i })).toBeInTheDocument();
    // "Input + output": the member chunk text remains visible.
    expect(screen.getByText('First chunk text.')).toBeInTheDocument();
  });
});
