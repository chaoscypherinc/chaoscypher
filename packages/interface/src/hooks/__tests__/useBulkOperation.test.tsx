// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { useBulkOperation } from '../useBulkOperation';

vi.mock('../../services/api/client', () => installApiClientMock());

describe('useBulkOperation', () => {
  it('starts with a closed, zero-progress state', () => {
    const { result } = renderHook(() => useBulkOperation());
    expect(result.current.progress.open).toBe(false);
    expect(result.current.progress.current).toBe(0);
    expect(result.current.progress.total).toBe(0);
    expect(result.current.progress.isComplete).toBe(false);
  });

  it('exposes an execute function and a ProgressDialog component', () => {
    const { result } = renderHook(() => useBulkOperation());
    expect(typeof result.current.execute).toBe('function');
    expect(typeof result.current.ProgressDialog).toBe('function');
  });

  it('opens the dialog when execute() is called', async () => {
    // Mock the first post() call to return a sync success envelope
    const client = await import('../../services/api/client');
    (client.apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: { success: 1, failed: 0, results: [], errors: [] },
    });

    const { result } = renderHook(() => useBulkOperation());

    // Fire-and-forget; the state transition we care about (open=true) happens
    // on the first setProgress call, before the network response resolves.
    act(() => {
      void result.current.execute('nodes', [
        { operation: 'delete', data: { id: 'test-node-1' } },
      ]);
    });

    expect(result.current.progress.open).toBe(true);
    expect(result.current.progress.total).toBe(1);
  });

  it('surfaces a failed task immediately instead of polling to timeout', async () => {
    // The failed-status throw used to sit inside the try whose catch only
    // rethrows HTTP 404s, so a plain Error was swallowed and the loop kept
    // polling a task it already knew was dead — ~60 further attempts, then a
    // generic "timeout" message instead of the backend's actual reason.
    vi.useFakeTimers();
    try {
      const client = await import('../../services/api/client');
      (client.apiClient.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        data: { task_id: 'task-1' },
      });
      const get = client.apiClient.get as ReturnType<typeof vi.fn>;
      get.mockResolvedValue({ data: { status: 'failed', error: 'node 7 is locked' } });

      const { result } = renderHook(() => useBulkOperation());

      let rejection: unknown;
      const pending = result.current
        .execute('nodes', [{ operation: 'delete', data: { id: 'n7' } }])
        .catch((e: unknown) => {
          rejection = e;
        });

      await vi.advanceTimersByTimeAsync(5000);
      await pending;

      expect((rejection as Error).message).toBe('node 7 is locked');
      // One status poll, not the full 60-attempt budget.
      expect(get).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
