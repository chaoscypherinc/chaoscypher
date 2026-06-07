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
});
