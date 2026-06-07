// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../../constants/config', () => ({
  BATCH_CONFIG: {
    EXPORT_MAX_ATTEMPTS: 5,
    POLLING_WAIT_MS: 1,
  },
}));

import { apiClient } from '../client';
import { enqueueAndWait } from '../queue';

const get = apiClient.get as ReturnType<typeof vi.fn>;
const post = apiClient.post as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('enqueueAndWait', () => {
  it('throws when the enqueue response has no task_id', async () => {
    post.mockResolvedValueOnce({ data: { status: 'queued' } });
    await expect(
      enqueueAndWait(() => apiClient.post('/some/endpoint') as unknown as Promise<{ data: { task_id: string } }>),
    ).rejects.toThrow(/task_id/);
  });

  it('returns the result when the task completes', async () => {
    post.mockResolvedValueOnce({ data: { task_id: 't-1' } });
    get
      .mockResolvedValueOnce({ data: { status: 'running' } })
      .mockResolvedValueOnce({ data: { status: 'completed' } })
      .mockResolvedValueOnce({ data: { result: { stats: 42 } } });

    const out = await enqueueAndWait<{ stats: number }>(
      () => apiClient.post('/x') as unknown as Promise<{ data: { task_id: string } }>,
    );

    expect(out).toEqual({ stats: 42 });
    // 2 polls + 1 final-result fetch
    expect(get).toHaveBeenCalledTimes(3);
    expect(get).toHaveBeenNthCalledWith(3, '/queue/tasks/t-1/result', expect.any(Object));
  });

  it('throws with the server error message when the task fails', async () => {
    post.mockResolvedValueOnce({ data: { task_id: 't-2' } });
    get.mockResolvedValueOnce({ data: { status: 'failed', error: 'rate limit' } });

    await expect(
      enqueueAndWait(() => apiClient.post('/x') as unknown as Promise<{ data: { task_id: string } }>),
    ).rejects.toThrow('rate limit');
  });

  it('throws a generic error when the task fails without a reason', async () => {
    post.mockResolvedValueOnce({ data: { task_id: 't-3' } });
    get.mockResolvedValueOnce({ data: { status: 'failed' } });

    await expect(
      enqueueAndWait(() => apiClient.post('/x') as unknown as Promise<{ data: { task_id: string } }>),
    ).rejects.toThrow(/failed without a reason/);
  });

  it('throws when a cancelled status is returned', async () => {
    post.mockResolvedValueOnce({ data: { task_id: 't-4' } });
    get.mockResolvedValueOnce({ data: { status: 'cancelled' } });

    await expect(
      enqueueAndWait(() => apiClient.post('/x') as unknown as Promise<{ data: { task_id: string } }>),
    ).rejects.toThrow(/cancelled/);
  });

  it('throws a timeout error when max attempts are exhausted', async () => {
    post.mockResolvedValueOnce({ data: { task_id: 't-5' } });
    // Always returns running — never completes.
    get.mockResolvedValue({ data: { status: 'running' } });

    await expect(
      enqueueAndWait(
        () => apiClient.post('/x') as unknown as Promise<{ data: { task_id: string } }>,
        { maxAttempts: 3, intervalMs: 1 },
      ),
    ).rejects.toThrow(/timeout/);
  });

  it('aborts when the signal is already aborted before the first poll', async () => {
    post.mockResolvedValueOnce({ data: { task_id: 't-6' } });
    const ctrl = new AbortController();
    ctrl.abort();

    await expect(
      enqueueAndWait(
        () => apiClient.post('/x') as unknown as Promise<{ data: { task_id: string } }>,
        { signal: ctrl.signal, intervalMs: 1 },
      ),
    ).rejects.toThrow(/Aborted|abort/i);
  });
});
