// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import { apiClient } from '../client';
import { getGraphSnapshot, refreshGraphSnapshot } from '../graphSnapshot';
import type { GraphBreakdown } from '../../../types/graphSnapshot';

const sampleBreakdown: GraphBreakdown = {
  version: 2,
  generated_at: '2026-05-19T00:00:00Z',
  database_name: 'test-db',
  title: 'Test',
  stats: { total_nodes: 3, total_edges: 1, total_sources: 1 },
  sources: [],
};

describe('getGraphSnapshot', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns null when the server responds 204', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 204,
      data: null,
    });

    const result = await getGraphSnapshot();

    expect(result).toBeNull();
    expect(apiClient.get).toHaveBeenCalledWith('/graph/snapshot');
  });

  it('returns the response body when the server responds 200', async () => {
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 200,
      data: sampleBreakdown,
    });

    const result = await getGraphSnapshot();

    expect(result).toEqual(sampleBreakdown);
    expect(apiClient.get).toHaveBeenCalledWith('/graph/snapshot');
  });

  it('returns the response body for any non-204 status (e.g. 200 with explicit null)', async () => {
    // Edge case: server responds 200 with body `null`. The 204 branch
    // doesn't fire (status !== 204), so we return res.data which is null.
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 200,
      data: null,
    });

    const result = await getGraphSnapshot();

    expect(result).toBeNull();
  });
});

describe('refreshGraphSnapshot', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns the task_id from the 202 envelope', async () => {
    (apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 202,
      data: { task_id: 'task-xyz' },
    });

    const result = await refreshGraphSnapshot();

    expect(result).toEqual({ task_id: 'task-xyz' });
    expect(apiClient.post).toHaveBeenCalledWith('/graph/snapshot/refresh');
  });
});
