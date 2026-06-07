// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useSourcesData tests.
 *
 * Pins the Sources-list data hook across its migration from
 * fetch+useState+usePolling to TanStack Query. Mocks at the apiClient layer so
 * the real service modules and query hooks run unchanged: the hook composes the
 * unified-list, domains, queue-stats, extraction-capacity, and quality-score
 * queries and adapts them to the page's expected return shape.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { apiClient } from '../../../../services/api/client';
import { useSourcesData, hasProcessingSources } from '../useSourcesData';

vi.mock('../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const FILTERS = { stage: 'all' as const, status: '', source_type: '', search: '' };

const PAGINATED_SOURCES = {
  data: [
    {
      id: 's1',
      title: 'First Source',
      status: 'committed',
      source_type: 'pdf',
      created_at: '2026-05-20T00:00:00Z',
      file_size: 1234,
      extraction_entities_count: 5,
      extraction_relationships_count: 3,
    },
    {
      id: 's2',
      title: 'Second Source',
      status: 'pending',
      source_type: 'txt',
      created_at: '2026-05-21T00:00:00Z',
      file_size: 999,
    },
  ],
  pagination: { total: 2, page: 1, page_size: 50, total_pages: 1, has_next: false, has_prev: false },
};

const DOMAINS = { domains: [{ name: 'general', description: 'General', builtin: true }] };
const QUEUE_STATS = {
  data: { estimated_completion_times_human: { llm: '2m', operations: '30s' } },
};
const SETTINGS = {
  llm: { ai_context_window: 16384 },
  chunking: { group_size: 8, small_chunk_size: 800, output_tokens_per_chunk: 3000 },
};
const QUALITY = {
  sources: [{ source_id: 's1', total_score: 42 }],
  total_sources: 1,
  avg_score: 42,
  avg_entity_quality: 80,
  avg_relationship_quality: 70,
};

function routeGet(url: string) {
  if (url === '/sources') return Promise.resolve({ data: PAGINATED_SOURCES });
  if (url === '/sources/domains') return Promise.resolve({ data: DOMAINS });
  if (url === '/llm/stats') return Promise.resolve({ data: QUEUE_STATS });
  if (url === '/settings') return Promise.resolve({ data: SETTINGS });
  return Promise.resolve({ data: {} });
}

describe('useSourcesData', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads sources and the derived list metadata', async () => {
    mockedApiClient.get.mockImplementation(routeGet);
    mockedApiClient.post.mockImplementation((url: string) =>
      url === '/quality/analyze' ? Promise.resolve({ data: QUALITY }) : Promise.resolve({ data: {} }),
    );

    const { result } = renderHook(() => useSourcesData(FILTERS), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.sources.length).toBe(2));

    // Newest first (sorted by created_at desc in listUnified).
    expect(result.current.sources[0].id).toBe('s2');
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();

    await waitFor(() => expect(result.current.domains.length).toBe(1));
    await waitFor(() =>
      expect(result.current.queueStats?.estimated_completion_times_human?.llm).toBe('2m'),
    );
    await waitFor(() => expect(result.current.extractionCapacity.contextWindow).toBe(16384));
    expect(result.current.extractionCapacity.groupSize).toBe(8);

    // Quality scores are loaded only for the active source with extraction data.
    await waitFor(() => expect(result.current.qualityScores.get('s1')?.total_score).toBe(42));
  });

  it('returns an empty list and no error when there are no sources', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/sources') {
        return Promise.resolve({
          data: { data: [], pagination: { total: 0, page: 1, page_size: 50, total_pages: 0, has_next: false, has_prev: false } },
        });
      }
      return routeGet(url);
    });

    const { result } = renderHook(() => useSourcesData(FILTERS), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.sources).toEqual([]);
    expect(result.current.error).toBeNull();
    // No active source with data → no quality scores requested.
    expect(result.current.qualityScores.size).toBe(0);
  });

  it('exposes a refresh that re-fetches the list', async () => {
    mockedApiClient.get.mockImplementation(routeGet);
    const { result } = renderHook(() => useSourcesData(FILTERS), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.sources.length).toBe(2));
    const callsBefore = mockedApiClient.get.mock.calls.filter((c) => c[0] === '/sources').length;

    await result.current.refresh();

    await waitFor(() => {
      const callsAfter = mockedApiClient.get.mock.calls.filter((c) => c[0] === '/sources').length;
      expect(callsAfter).toBeGreaterThan(callsBefore);
    });
  });
});

describe('useSourcesData — poll-terminal statuses', () => {
  it('treats awaiting_confirmation as settled (no auto-poll)', () => {
    expect(
      hasProcessingSources([
        {
          id: 'a',
          stage: 'queued',
          title: 't',
          source_type: 'pdf',
          size: 1,
          status: 'awaiting_confirmation',
          created_at: '2026-05-28T00:00:00Z',
        },
      ]),
    ).toBe(false);
  });

  it('still polls while a genuinely processing source exists', () => {
    expect(
      hasProcessingSources([
        {
          id: 'b',
          stage: 'processing',
          title: 't',
          source_type: 'pdf',
          size: 1,
          status: 'indexing',
          created_at: '2026-05-28T00:00:00Z',
        },
      ]),
    ).toBe(true);
  });
});
