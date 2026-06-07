// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for ``useLLMProcessing`` after its migration to TanStack Query
 * (2026-05-18).
 *
 * The hook exposes ``stats`` / ``chartTasks`` / ``selectedTask`` queries
 * keyed under ``['source', sourceId, …]`` so a parent invalidation
 * (e.g. from ``useChunkRerun``) cascades to all of them. Selection state
 * (``selectedChunkId``) stays local — flipping it gates the on-demand
 * heavy-task fetch.
 */

import React from 'react';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../../../services/api/sources', () => ({
  sourcesApi: {
    getExtractionTaskStats: vi.fn(async () => ({
      total_tasks: 1,
      total_entities: 5,
      total_relationships: 2,
      avg_entities_per_task: 5,
      avg_relationships_per_task: 2,
      total_retries: 0,
      max_retries_single_task: 0,
      total_invalid_relationships: 0,
      avg_invalid_per_task: 0,
      total_entities_filtered: 0,
      total_relationships_filtered: 0,
    })),
    getExtractionTasksForCharts: vi.fn(async () => [
      {
        id: 'c1',
        chunk_index: 1,
        status: 'completed',
        retry_count: 0,
        entity_count: 5,
        relationship_count: 2,
        invalid_relationship_count: 0,
      },
    ]),
    getExtractionTask: vi.fn(async (_sourceId: string, taskId: string) => ({
      id: taskId,
      chunk_index: 1,
      status: 'completed',
      retry_count: 0,
      entity_count: 5,
      relationship_count: 2,
      invalid_relationship_count: 0,
      input_text: 'x',
      llm_response_json: '{}',
      filtering_log: null,
    })),
  },
}));

import { useLLMProcessing } from '../hooks/useLLMProcessing';

function wrap({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('useLLMProcessing (TanStack Query)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('exposes selectedChunkId in initial state as null', () => {
    const { result } = renderHook(() => useLLMProcessing('s1', true), { wrapper: wrap });
    expect(result.current.state.selectedChunkId).toBeNull();
    expect(result.current.state.selectedTask).toBeNull();
    expect(result.current.state.selectedTaskLoading).toBe(false);
  });

  it('selectChunk(id) sets selectedChunkId and triggers detail fetch', async () => {
    const { result } = renderHook(() => useLLMProcessing('s1', true), { wrapper: wrap });
    act(() => {
      result.current.selectChunk('c1');
    });
    await waitFor(() => expect(result.current.state.selectedChunkId).toBe('c1'));
    await waitFor(() => expect(result.current.state.selectedTask).not.toBeNull());
    expect(result.current.state.selectedTask?.id).toBe('c1');
  });

  it('selectChunk(null) clears the selection', async () => {
    const { result } = renderHook(() => useLLMProcessing('s1', true), { wrapper: wrap });
    act(() => {
      result.current.selectChunk('c1');
    });
    await waitFor(() => expect(result.current.state.selectedChunkId).toBe('c1'));
    act(() => {
      result.current.selectChunk(null);
    });
    await waitFor(() => expect(result.current.state.selectedChunkId).toBeNull());
    expect(result.current.state.selectedTask).toBeNull();
  });

  it('loads chartTasks and stats via TanStack queries on mount', async () => {
    const { result } = renderHook(() => useLLMProcessing('s1', true), { wrapper: wrap });
    await waitFor(() => expect(result.current.state.chartTasks.length).toBe(1));
    expect(result.current.state.stats).not.toBeNull();
  });

  it('skips fetching while disabled', async () => {
    const api = await import('../../../../../services/api/sources');
    const { result } = renderHook(() => useLLMProcessing('s1', false), { wrapper: wrap });
    // Allow microtasks to flush; queries should never have fired.
    await new Promise((r) => setTimeout(r, 0));
    expect(api.sourcesApi.getExtractionTaskStats).not.toHaveBeenCalled();
    expect(api.sourcesApi.getExtractionTasksForCharts).not.toHaveBeenCalled();
    expect(result.current.state.chartTasks).toEqual([]);
    expect(result.current.state.stats).toBeNull();
  });
});
