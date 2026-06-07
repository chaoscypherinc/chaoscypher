// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useLogViewer — log-viewer state hook.
 *
 * Strategy:
 * - Mock useAppConfig to return fixed config values.
 * - Mock logsApi.getAll / logsApi.getService / logsApi.getStatus as vi.fn().
 * - Mock usePolling to capture the onPoll callbacks for manual invocation,
 *   giving deterministic control over fetch timing without timers.
 * - renderHook + act + waitFor from @testing-library/react.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import type { LogResponse, ServiceStatusResponse } from '../../../../services/api/logs';
import type { ServiceTab } from '../useLogViewer';

// ---------------------------------------------------------------------------
// Captured poll callbacks (filled in by the usePolling mock)
// ---------------------------------------------------------------------------

let capturedLogPoll: (() => Promise<void>) | null = null;
let capturedStatusPoll: (() => Promise<void>) | null = null;
let capturedLogEnabled: boolean = true;

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../../../../contexts/useAppConfig', () => ({
  useAppConfig: () => ({
    intervals_log_poll_ms: 5000,
    intervals_status_poll_ms: 10000,
    intervals_log_initial_lines: 200,
    intervals_log_poll_lines: 50,
  }),
}));

vi.mock('../../../../services/api/logs', () => ({
  logsApi: {
    getAll: vi.fn<(lines: number) => Promise<LogResponse>>(),
    getService: vi.fn<(service: string, lines: number) => Promise<LogResponse>>(),
    getStatus: vi.fn<() => Promise<ServiceStatusResponse>>(),
  },
}));

vi.mock('../../../../hooks/usePolling', () => ({
  usePolling: vi.fn(
    (opts: {
      onPoll: () => Promise<void>;
      interval: number;
      enabled: boolean;
      pauseWhenHidden: boolean;
      immediate: boolean;
    }) => {
      // The hook is called twice — first call = log poll, second = status poll.
      // We distinguish by interval value (5000 vs 10000).
      if (opts.interval === 5000) {
        capturedLogPoll = opts.onPoll;
        capturedLogEnabled = opts.enabled;
      } else {
        capturedStatusPoll = opts.onPoll;
      }
    },
  ),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function importDeps() {
  const { logsApi } = await import('../../../../services/api/logs');
  const { useLogViewer } = await import('../useLogViewer');
  return { logsApi, useLogViewer };
}

function makeLogResponse(overrides?: Partial<LogResponse>): LogResponse {
  return {
    service: null,
    lines: ['line 1', 'line 2'],
    total_lines: 2,
    ...overrides,
  };
}

function makeStatusResponse(overrides?: Partial<ServiceStatusResponse>): ServiceStatusResponse {
  return {
    available: true,
    services: [
      {
        name: 'cortex',
        state: 'running',
        pid: 123,
        uptime_seconds: 60,
        start_time: '2026-05-25T08:00:00Z',
        description: 'API server',
      },
    ],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Test setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  capturedLogPoll = null;
  capturedStatusPoll = null;
  capturedLogEnabled = true;
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Suite: initial state
// ---------------------------------------------------------------------------

describe('useLogViewer — initial state', () => {
  it('starts with activeTab = "all"', async () => {
    const { useLogViewer } = await importDeps();
    const { result } = renderHook(() => useLogViewer());
    expect(result.current.activeTab).toBe('all');
  });

  it('starts with empty lines array', async () => {
    const { useLogViewer } = await importDeps();
    const { result } = renderHook(() => useLogViewer());
    expect(result.current.lines).toEqual([]);
  });

  it('starts with totalLines = 0', async () => {
    const { useLogViewer } = await importDeps();
    const { result } = renderHook(() => useLogViewer());
    expect(result.current.totalLines).toBe(0);
  });

  it('starts with loading = true', async () => {
    const { useLogViewer } = await importDeps();
    const { result } = renderHook(() => useLogViewer());
    expect(result.current.loading).toBe(true);
  });

  it('starts with paused = false', async () => {
    const { useLogViewer } = await importDeps();
    const { result } = renderHook(() => useLogViewer());
    expect(result.current.paused).toBe(false);
  });

  it('starts with error = null', async () => {
    const { useLogViewer } = await importDeps();
    const { result } = renderHook(() => useLogViewer());
    expect(result.current.error).toBeNull();
  });

  it('starts with status = null', async () => {
    const { useLogViewer } = await importDeps();
    const { result } = renderHook(() => useLogViewer());
    expect(result.current.status).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Suite: polling callback — log fetch (success path, all tab)
// ---------------------------------------------------------------------------

describe('useLogViewer — log poll callback (all tab)', () => {
  it('calls logsApi.getAll with INITIAL_LINES on first poll', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    (logsApi.getAll as Mock).mockResolvedValueOnce(makeLogResponse());
    (logsApi.getStatus as Mock).mockResolvedValue(makeStatusResponse());

    renderHook(() => useLogViewer());

    expect(capturedLogPoll).not.toBeNull();
    await act(async () => {
      await capturedLogPoll!();
    });

    expect(logsApi.getAll).toHaveBeenCalledWith(200);
  });

  it('uses POLL_LINES on subsequent polls', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    (logsApi.getAll as Mock).mockResolvedValue(makeLogResponse());
    (logsApi.getStatus as Mock).mockResolvedValue(makeStatusResponse());

    renderHook(() => useLogViewer());

    // First poll (initial)
    await act(async () => {
      await capturedLogPoll!();
    });
    // Second poll (subsequent)
    await act(async () => {
      await capturedLogPoll!();
    });

    const calls = (logsApi.getAll as Mock).mock.calls;
    expect(calls[0][0]).toBe(200); // initial
    expect(calls[1][0]).toBe(50);  // poll
  });

  it('updates lines and totalLines after a successful poll', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    const response = makeLogResponse({ lines: ['a', 'b', 'c'], total_lines: 3 });
    (logsApi.getAll as Mock).mockResolvedValueOnce(response);

    const { result } = renderHook(() => useLogViewer());

    await act(async () => {
      await capturedLogPoll!();
    });

    expect(result.current.lines).toEqual(['a', 'b', 'c']);
    expect(result.current.totalLines).toBe(3);
  });

  it('sets loading = false after a successful poll', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    (logsApi.getAll as Mock).mockResolvedValueOnce(makeLogResponse());

    const { result } = renderHook(() => useLogViewer());
    expect(result.current.loading).toBe(true);

    await act(async () => {
      await capturedLogPoll!();
    });

    expect(result.current.loading).toBe(false);
  });

  it('clears error after a successful poll', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    // First poll fails
    (logsApi.getAll as Mock).mockRejectedValueOnce(new Error('net error'));
    // Second poll succeeds
    (logsApi.getAll as Mock).mockResolvedValueOnce(makeLogResponse());

    const { result } = renderHook(() => useLogViewer());

    await act(async () => {
      await capturedLogPoll!();
    });
    expect(result.current.error).toBe('Failed to fetch logs');

    await act(async () => {
      await capturedLogPoll!();
    });
    expect(result.current.error).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Suite: polling callback — error path
// ---------------------------------------------------------------------------

describe('useLogViewer — log poll callback (error path)', () => {
  it('sets error = "Failed to fetch logs" on API rejection', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    (logsApi.getAll as Mock).mockRejectedValueOnce(new Error('timeout'));

    const { result } = renderHook(() => useLogViewer());

    await act(async () => {
      await capturedLogPoll!();
    });

    expect(result.current.error).toBe('Failed to fetch logs');
  });

  it('still sets loading = false even when API rejects', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    (logsApi.getAll as Mock).mockRejectedValueOnce(new Error('timeout'));

    const { result } = renderHook(() => useLogViewer());

    await act(async () => {
      await capturedLogPoll!();
    });

    expect(result.current.loading).toBe(false);
  });

  it('does not update lines when API rejects', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    // First poll succeeds with some lines
    (logsApi.getAll as Mock).mockResolvedValueOnce(makeLogResponse({ lines: ['kept'] }));
    // Second poll fails
    (logsApi.getAll as Mock).mockRejectedValueOnce(new Error('bad'));

    const { result } = renderHook(() => useLogViewer());

    await act(async () => { await capturedLogPoll!(); });
    await act(async () => { await capturedLogPoll!(); });

    // Lines from first poll should be preserved
    expect(result.current.lines).toEqual(['kept']);
  });
});

// ---------------------------------------------------------------------------
// Suite: polling callback — service tab (non-"all")
// ---------------------------------------------------------------------------

describe('useLogViewer — log poll callback (service tab)', () => {
  it('calls logsApi.getService when activeTab is not "all"', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    (logsApi.getService as Mock).mockResolvedValue(
      makeLogResponse({ service: 'cortex' }),
    );

    const { result } = renderHook(() => useLogViewer());

    // Switch to cortex tab
    act(() => {
      result.current.setActiveTab('cortex');
    });

    // The hook re-renders with new activeTab; capturedLogPoll now holds the
    // new fetchLogs closure (from the latest usePolling call). Invoke it.
    await act(async () => {
      await capturedLogPoll!();
    });

    expect(logsApi.getService).toHaveBeenCalledWith('cortex', expect.any(Number));
    expect(logsApi.getAll).not.toHaveBeenCalled();
  });

  it('uses INITIAL_LINES again after tab switch', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    (logsApi.getAll as Mock).mockResolvedValue(makeLogResponse());
    (logsApi.getService as Mock).mockResolvedValue(makeLogResponse({ service: 'neuron' }));

    const { result } = renderHook(() => useLogViewer());

    // Initial fetch (uses INITIAL_LINES=200)
    await act(async () => { await capturedLogPoll!(); });

    // Switch tab — resets isInitialFetch flag
    act(() => { result.current.setActiveTab('neuron'); });

    // Next fetch should use INITIAL_LINES again
    await act(async () => { await capturedLogPoll!(); });

    const calls = (logsApi.getService as Mock).mock.calls;
    expect(calls[0][1]).toBe(200);
  });
});

// ---------------------------------------------------------------------------
// Suite: setActiveTab
// ---------------------------------------------------------------------------

describe('useLogViewer — setActiveTab', () => {
  it('updates activeTab', async () => {
    const { useLogViewer } = await importDeps();
    const { result } = renderHook(() => useLogViewer());

    act(() => { result.current.setActiveTab('nginx'); });

    await waitFor(() => {
      expect(result.current.activeTab).toBe('nginx');
    });
  });

  it('sets loading = true when tab changes', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    (logsApi.getAll as Mock).mockResolvedValue(makeLogResponse());

    const { result } = renderHook(() => useLogViewer());

    // Complete first poll so loading goes false
    await act(async () => { await capturedLogPoll!(); });
    expect(result.current.loading).toBe(false);

    // Switch tab — loading should flip to true
    act(() => { result.current.setActiveTab('valkey'); });
    expect(result.current.loading).toBe(true);
  });

  it('accepts all valid ServiceTab values', async () => {
    const { useLogViewer } = await importDeps();
    const validTabs: ServiceTab[] = ['all', 'cortex', 'neuron', 'nginx', 'valkey'];
    const { result } = renderHook(() => useLogViewer());

    for (const tab of validTabs) {
      act(() => { result.current.setActiveTab(tab); });
      await waitFor(() => {
        expect(result.current.activeTab).toBe(tab);
      });
    }
  });
});

// ---------------------------------------------------------------------------
// Suite: status poll callback
// ---------------------------------------------------------------------------

describe('useLogViewer — status poll callback', () => {
  it('populates status after getStatus resolves', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    const statusResponse = makeStatusResponse();
    (logsApi.getStatus as Mock).mockResolvedValueOnce(statusResponse);

    const { result } = renderHook(() => useLogViewer());

    expect(capturedStatusPoll).not.toBeNull();
    await act(async () => {
      await capturedStatusPoll!();
    });

    expect(result.current.status).toEqual(statusResponse);
  });

  it('does not throw or update status when getStatus rejects', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    (logsApi.getStatus as Mock).mockRejectedValueOnce(new Error('status unavailable'));

    const { result } = renderHook(() => useLogViewer());

    // Should resolve without throwing
    await act(async () => {
      await capturedStatusPoll!();
    });

    expect(result.current.status).toBeNull();
  });

  it('updates status on repeated polls', async () => {
    const { logsApi, useLogViewer } = await importDeps();
    const first = makeStatusResponse();
    const second = makeStatusResponse({
      services: [
        {
          name: 'neuron',
          state: 'stopped',
          pid: null,
          uptime_seconds: null,
          start_time: null,
          description: 'Worker',
        },
      ],
    });

    (logsApi.getStatus as Mock)
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second);

    const { result } = renderHook(() => useLogViewer());

    await act(async () => { await capturedStatusPoll!(); });
    expect(result.current.status).toEqual(first);

    await act(async () => { await capturedStatusPoll!(); });
    expect(result.current.status).toEqual(second);
  });
});

// ---------------------------------------------------------------------------
// Suite: togglePause
// ---------------------------------------------------------------------------

describe('useLogViewer — togglePause', () => {
  it('toggles paused from false to true', async () => {
    const { useLogViewer } = await importDeps();
    const { result } = renderHook(() => useLogViewer());

    expect(result.current.paused).toBe(false);

    act(() => { result.current.togglePause(); });

    expect(result.current.paused).toBe(true);
  });

  it('toggles paused back to false on second call', async () => {
    const { useLogViewer } = await importDeps();
    const { result } = renderHook(() => useLogViewer());

    act(() => { result.current.togglePause(); });
    act(() => { result.current.togglePause(); });

    expect(result.current.paused).toBe(false);
  });

  it('passes enabled=false to log usePolling when paused', async () => {
    const { usePolling } = await import('../../../../hooks/usePolling');
    const { useLogViewer } = await importDeps();

    const { result } = renderHook(() => useLogViewer());

    act(() => { result.current.togglePause(); });

    // After re-render, usePolling was called again — capturedLogEnabled reflects
    // the latest enabled value passed to the log-poll usePolling instance.
    expect(capturedLogEnabled).toBe(false);

    // Verify usePolling mock was invoked
    expect(usePolling).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Suite: return shape
// ---------------------------------------------------------------------------

describe('useLogViewer — return shape', () => {
  it('exposes all expected properties', async () => {
    const { useLogViewer } = await importDeps();
    const { result } = renderHook(() => useLogViewer());

    expect(result.current).toMatchObject({
      activeTab: expect.any(String),
      setActiveTab: expect.any(Function),
      lines: expect.any(Array),
      totalLines: expect.any(Number),
      status: null,
      loading: expect.any(Boolean),
      paused: expect.any(Boolean),
      togglePause: expect.any(Function),
      error: null,
    });
  });
});
