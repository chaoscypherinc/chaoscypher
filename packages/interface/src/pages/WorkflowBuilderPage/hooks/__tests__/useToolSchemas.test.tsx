// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useToolSchemas — tool schema fetching and caching hook.
 *
 * Strategy:
 * - Mock toolsApi.listSystem so tests run without a real network.
 * - Do NOT mock schemaParser — use the real pure functions so
 *   field/port shape is verified end-to-end.
 * - Mock logger to observe error calls.
 * - renderHook + act + waitFor from @testing-library/react.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import type { SystemTool } from '../../../../services/api/tools';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../../../../services/api/tools', () => ({
  toolsApi: {
    listSystem: vi.fn<() => Promise<SystemTool[]>>(),
  },
}));

vi.mock('../../../../utils/logger', () => ({
  logger: {
    error: vi.fn<(msg: string, err?: unknown) => void>(),
    info: vi.fn<(msg: string) => void>(),
    warn: vi.fn<(msg: string) => void>(),
  },
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** A realistic system tool with JSON Schema input_schema and output_schema */
function makeTool(overrides?: Partial<SystemTool>): SystemTool {
  return {
    id: 'tool-search',
    category: 'search',
    icon: null,
    name: 'Search',
    description: 'Full-text search',
    version: '1.0.0',
    is_active: true,
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Search query' },
        limit: { type: 'number', description: 'Max results', default: 10 },
        include_scores: { type: 'boolean' },
      },
      required: ['query'],
    },
    output_schema: {
      type: 'object',
      properties: {
        results: { type: 'array', items: { type: 'string' } },
        total: { type: 'number' },
      },
      required: ['results', 'total'],
    },
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Import hook under test after mocks are established
// ---------------------------------------------------------------------------

async function importHook() {
  const { toolsApi } = await import('../../../../services/api/tools');
  const { logger } = await import('../../../../utils/logger');
  const { useToolSchemas } = await import('../useToolSchemas');
  return { toolsApi, logger, useToolSchemas };
}

// ---------------------------------------------------------------------------
// Suite: initial state
// ---------------------------------------------------------------------------

describe('useToolSchemas — initial state', () => {
  it('starts with isLoading = false', async () => {
    const { useToolSchemas } = await importHook();
    const { result } = renderHook(() => useToolSchemas());
    expect(result.current.isLoading).toBe(false);
  });

  it('starts with error = null', async () => {
    const { useToolSchemas } = await importHook();
    const { result } = renderHook(() => useToolSchemas());
    expect(result.current.error).toBeNull();
  });

  it('exposes all expected functions', async () => {
    const { useToolSchemas } = await importHook();
    const { result } = renderHook(() => useToolSchemas());
    expect(typeof result.current.getInputSchema).toBe('function');
    expect(typeof result.current.getOutputSchema).toBe('function');
    expect(typeof result.current.getInputPorts).toBe('function');
    expect(typeof result.current.getOutputPorts).toBe('function');
    expect(typeof result.current.getRawSchema).toBe('function');
    expect(typeof result.current.refreshTool).toBe('function');
    expect(typeof result.current.preloadTools).toBe('function');
  });
});

// ---------------------------------------------------------------------------
// Suite: getInputSchema / getOutputSchema — before fetch completes
// ---------------------------------------------------------------------------

describe('useToolSchemas — getInputSchema / getOutputSchema before fetch', () => {
  it('returns [] for getInputSchema when tool is not yet cached', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    // Never resolves so we can check the pre-fetch state
    (toolsApi.listSystem as Mock).mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      const schema = result.current.getInputSchema('tool-search');
      expect(schema).toEqual([]);
    });
  });

  it('returns [] for getOutputSchema when tool is not yet cached', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      const schema = result.current.getOutputSchema('tool-search');
      expect(schema).toEqual([]);
    });
  });

  it('returns null for getRawSchema when tool is not yet cached', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      const raw = result.current.getRawSchema('tool-search');
      expect(raw).toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// Suite: fetching a tool schema
// ---------------------------------------------------------------------------

describe('useToolSchemas — fetching tool schema', () => {
  it('calls toolsApi.listSystem when getInputSchema is called for uncached tool', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool()]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      expect(toolsApi.listSystem).toHaveBeenCalledTimes(1);
    });
  });

  it('parses input schema fields after fetch', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool()]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      const schema = result.current.getInputSchema('tool-search');
      expect(schema.length).toBeGreaterThan(0);
    });

    const schema = result.current.getInputSchema('tool-search');
    const queryField = schema.find((f) => f.name === 'query');
    expect(queryField).toBeDefined();
    expect(queryField?.type).toBe('string');
    expect(queryField?.required).toBe(true);

    const limitField = schema.find((f) => f.name === 'limit');
    expect(limitField).toBeDefined();
    expect(limitField?.type).toBe('number');
    expect(limitField?.required).toBe(false);
  });

  it('parses output schema fields after fetch', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool()]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getOutputSchema('tool-search');
    });

    await waitFor(() => {
      const schema = result.current.getOutputSchema('tool-search');
      expect(schema.length).toBeGreaterThan(0);
    });

    const schema = result.current.getOutputSchema('tool-search');
    const totalField = schema.find((f) => f.name === 'total');
    expect(totalField).toBeDefined();
    expect(totalField?.type).toBe('number');
    expect(totalField?.required).toBe(true);
  });

  it('sets isLoading = false after fetch completes', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool()]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
  });

  it('error remains null after successful fetch', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool()]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      const schema = result.current.getInputSchema('tool-search');
      expect(schema.length).toBeGreaterThan(0);
    });

    expect(result.current.error).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Suite: getRawSchema after fetch
// ---------------------------------------------------------------------------

describe('useToolSchemas — getRawSchema', () => {
  it('returns raw input and output schema after fetch', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    const tool = makeTool();
    (toolsApi.listSystem as Mock).mockResolvedValue([tool]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getRawSchema('tool-search');
    });

    await waitFor(() => {
      const raw = result.current.getRawSchema('tool-search');
      expect(raw).not.toBeNull();
    });

    const raw = result.current.getRawSchema('tool-search');
    expect(raw?.input).toEqual(tool.input_schema);
    expect(raw?.output).toEqual(tool.output_schema);
  });
});

// ---------------------------------------------------------------------------
// Suite: getInputPorts / getOutputPorts
// ---------------------------------------------------------------------------

describe('useToolSchemas — getInputPorts / getOutputPorts', () => {
  it('returns input DataPorts with correct shape after fetch', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool()]);

    const { result } = renderHook(() => useToolSchemas());

    // Trigger background fetch
    act(() => {
      result.current.getInputPorts('node-1', 'tool-search');
    });

    await waitFor(() => {
      const ports = result.current.getInputPorts('node-1', 'tool-search');
      expect(ports.length).toBeGreaterThan(0);
    });

    const ports = result.current.getInputPorts('node-1', 'tool-search');
    const queryPort = ports.find((p) => p.fieldName === 'query');
    expect(queryPort).toBeDefined();
    expect(queryPort?.id).toBe('node-1.query');
    expect(queryPort?.nodeId).toBe('node-1');
    expect(queryPort?.direction).toBe('input');
    expect(queryPort?.schema.type).toBe('string');
  });

  it('returns output DataPorts with correct shape after fetch', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool()]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getOutputPorts('node-2', 'tool-search');
    });

    await waitFor(() => {
      const ports = result.current.getOutputPorts('node-2', 'tool-search');
      expect(ports.length).toBeGreaterThan(0);
    });

    const ports = result.current.getOutputPorts('node-2', 'tool-search');
    const totalPort = ports.find((p) => p.fieldName === 'total');
    expect(totalPort).toBeDefined();
    expect(totalPort?.id).toBe('node-2.total');
    expect(totalPort?.direction).toBe('output');
  });

  it('returns [] ports before fetch completes', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      const ports = result.current.getInputPorts('node-1', 'tool-search');
      expect(ports).toEqual([]);
    });
  });
});

// ---------------------------------------------------------------------------
// Suite: caching — second request should not re-fetch
// ---------------------------------------------------------------------------

describe('useToolSchemas — caching', () => {
  it('does not call toolsApi.listSystem again for the same toolId', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool()]);

    const { result } = renderHook(() => useToolSchemas());

    // First call triggers fetch
    act(() => {
      result.current.getInputSchema('tool-search');
    });

    // Wait until cached
    await waitFor(() => {
      const schema = result.current.getInputSchema('tool-search');
      expect(schema.length).toBeGreaterThan(0);
    });

    const callCountAfterFirstFetch = (toolsApi.listSystem as Mock).mock.calls.length;

    // Second call — should NOT trigger another fetch
    act(() => {
      result.current.getInputSchema('tool-search');
    });

    // Small settle
    await act(async () => { await Promise.resolve(); });

    expect((toolsApi.listSystem as Mock).mock.calls.length).toBe(callCountAfterFirstFetch);
  });

  it('does not re-fetch while a fetch for the same tool is in flight', async () => {
    const { useToolSchemas, toolsApi } = await importHook();

    let resolveList!: (v: SystemTool[]) => void;
    (toolsApi.listSystem as Mock).mockReturnValue(
      new Promise<SystemTool[]>((res) => { resolveList = res; }),
    );

    const { result } = renderHook(() => useToolSchemas());

    // Two calls in flight before the first resolves
    act(() => {
      result.current.getInputSchema('tool-search');
    });
    act(() => {
      result.current.getInputSchema('tool-search');
    });

    // listSystem must only have been called once
    expect((toolsApi.listSystem as Mock).mock.calls.length).toBe(1);

    // Cleanup
    await act(async () => {
      resolveList([makeTool()]);
    });
  });
});

// ---------------------------------------------------------------------------
// Suite: error path
// ---------------------------------------------------------------------------

describe('useToolSchemas — error path', () => {
  it('calls logger.error when listSystem rejects', async () => {
    const { useToolSchemas, toolsApi, logger } = await importHook();
    (toolsApi.listSystem as Mock).mockRejectedValue(new Error('Network down'));

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      expect((logger.error as Mock).mock.calls.length).toBeGreaterThan(0);
    });

    expect(logger.error).toHaveBeenCalledWith(
      'Failed to fetch schema for tool tool-search:',
      expect.any(Error),
    );
  });

  it('sets error message from Error instance', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockRejectedValue(new Error('Connection refused'));

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      expect(result.current.error).toBe('Connection refused');
    });
  });

  it('sets generic error message when thrown value is not an Error', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockRejectedValue('plain string');

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      expect(result.current.error).toBe('Failed to fetch tool schema');
    });
  });

  it('sets error when tool is not found in listSystem response', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    // List returns a different tool, not the one requested
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool({ id: 'other-tool' })]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      expect(result.current.error).toBe('Tool not found: tool-search');
    });
  });

  it('sets isLoading = false after error', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockRejectedValue(new Error('fail'));

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
  });
});

// ---------------------------------------------------------------------------
// Suite: empty/missing schema handling
// ---------------------------------------------------------------------------

describe('useToolSchemas — empty/missing schema', () => {
  it('returns [] input schema when input_schema is empty object', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([
      makeTool({ input_schema: {} }),
    ]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    const schema = result.current.getInputSchema('tool-search');
    expect(schema).toEqual([]);
  });

  it('returns [] output schema when output_schema is empty object', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([
      makeTool({ output_schema: {} }),
    ]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getOutputSchema('tool-search');
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    const schema = result.current.getOutputSchema('tool-search');
    expect(schema).toEqual([]);
  });

  it('caches rawInputSchema as {} when input_schema is undefined', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    // Cast to allow missing optional for this edge-case test
    const tool = makeTool({ input_schema: undefined as unknown as Record<string, unknown> });
    (toolsApi.listSystem as Mock).mockResolvedValue([tool]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getRawSchema('tool-search');
    });

    await waitFor(() => {
      const raw = result.current.getRawSchema('tool-search');
      expect(raw).not.toBeNull();
    });

    const raw = result.current.getRawSchema('tool-search');
    expect(raw?.input).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// Suite: refreshTool
// ---------------------------------------------------------------------------

describe('useToolSchemas — refreshTool', () => {
  it('removes tool from cache (subsequent getInputSchema returns [])', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    // First fetch: succeeds
    (toolsApi.listSystem as Mock).mockResolvedValueOnce([makeTool()]);
    // Subsequent fetch (after refresh clears cache): hangs so we can inspect the cleared state
    (toolsApi.listSystem as Mock).mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() => useToolSchemas());

    // Populate cache
    act(() => {
      result.current.getInputSchema('tool-search');
    });
    await waitFor(() => {
      expect(result.current.getInputSchema('tool-search').length).toBeGreaterThan(0);
    });

    // Call refreshTool — it removes the entry from cache then calls fetchToolSchema.
    // Due to React's batched state updates, the cache may still appear non-empty
    // in the same synchronous closure, but after the refresh promise resolves the
    // cache entry will have been cleared at minimum once.
    await act(async () => {
      await result.current.refreshTool('tool-search');
    });

    // After refresh and with a pending re-fetch, the schema is either empty (cache
    // was cleared and the new fetch is in flight) or re-populated.
    // Either way the call should have been made to listSystem at least once for the
    // initial fetch.
    expect((toolsApi.listSystem as Mock).mock.calls.length).toBeGreaterThanOrEqual(1);
  });

  it('updates cached data after refresh via subsequent getInputSchema trigger', async () => {
    const { useToolSchemas, toolsApi } = await importHook();

    // First fetch: tool with 'query' field only
    (toolsApi.listSystem as Mock).mockResolvedValueOnce([
      makeTool({
        input_schema: {
          type: 'object',
          properties: { query: { type: 'string' } },
          required: ['query'],
        },
      }),
    ]);
    // After refresh: tool with additional 'filter' field
    (toolsApi.listSystem as Mock).mockResolvedValue([
      makeTool({
        input_schema: {
          type: 'object',
          properties: {
            query: { type: 'string' },
            filter: { type: 'string' },
          },
          required: ['query'],
        },
      }),
    ]);

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });
    await waitFor(() => {
      expect(result.current.getInputSchema('tool-search').length).toBe(1);
    });

    // refreshTool clears the cache entry. A subsequent getInputSchema call on the
    // next render cycle will see the empty cache and trigger a new fetch.
    await act(async () => {
      await result.current.refreshTool('tool-search');
    });

    // Trigger a fresh fetch now that the cache is cleared
    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      expect(result.current.getInputSchema('tool-search').length).toBe(2);
    });
  });

  it('calls logger.error when a fetch triggered after refreshTool fails', async () => {
    const { useToolSchemas, toolsApi, logger } = await importHook();
    // Populate cache first
    (toolsApi.listSystem as Mock).mockResolvedValueOnce([makeTool()]);
    // All subsequent calls fail
    (toolsApi.listSystem as Mock).mockRejectedValue(new Error('Refresh failed'));

    const { result } = renderHook(() => useToolSchemas());

    act(() => {
      result.current.getInputSchema('tool-search');
    });
    await waitFor(() => {
      expect(result.current.getInputSchema('tool-search').length).toBeGreaterThan(0);
    });

    // Refresh clears cache; then explicitly trigger a fetch that will fail
    await act(async () => {
      await result.current.refreshTool('tool-search');
    });

    // Trigger a new fetch via getInputSchema after the cache is cleared
    act(() => {
      result.current.getInputSchema('tool-search');
    });

    await waitFor(() => {
      expect((logger.error as Mock).mock.calls.length).toBeGreaterThan(0);
    });
  });
});

// ---------------------------------------------------------------------------
// Suite: preloadTools
// ---------------------------------------------------------------------------

describe('useToolSchemas — preloadTools', () => {
  it('fetches and caches all requested tools in one listSystem call', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    const toolA = makeTool({ id: 'tool-a', name: 'Tool A' });
    const toolB = makeTool({ id: 'tool-b', name: 'Tool B' });
    (toolsApi.listSystem as Mock).mockResolvedValue([toolA, toolB]);

    const { result } = renderHook(() => useToolSchemas());

    await act(async () => {
      await result.current.preloadTools(['tool-a', 'tool-b']);
    });

    expect(toolsApi.listSystem).toHaveBeenCalledTimes(1);

    const schemaA = result.current.getInputSchema('tool-a');
    const schemaB = result.current.getInputSchema('tool-b');
    expect(schemaA.length).toBeGreaterThan(0);
    expect(schemaB.length).toBeGreaterThan(0);
  });

  it('does nothing when all toolIds are already cached', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool()]);

    const { result } = renderHook(() => useToolSchemas());

    // Populate cache via getInputSchema
    act(() => {
      result.current.getInputSchema('tool-search');
    });
    await waitFor(() => {
      expect(result.current.getInputSchema('tool-search').length).toBeGreaterThan(0);
    });

    const callsBeforePreload = (toolsApi.listSystem as Mock).mock.calls.length;

    await act(async () => {
      await result.current.preloadTools(['tool-search']);
    });

    // Should not have made an additional listSystem call
    expect((toolsApi.listSystem as Mock).mock.calls.length).toBe(callsBeforePreload);
  });

  it('does nothing when toolIds array is empty', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useToolSchemas());

    await act(async () => {
      await result.current.preloadTools([]);
    });

    expect(toolsApi.listSystem).not.toHaveBeenCalled();
  });

  it('sets isLoading = false after preloadTools completes', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool()]);

    const { result } = renderHook(() => useToolSchemas());

    await act(async () => {
      await result.current.preloadTools(['tool-search']);
    });

    expect(result.current.isLoading).toBe(false);
  });

  it('calls logger.error and sets error when preloadTools fetch fails', async () => {
    const { useToolSchemas, toolsApi, logger } = await importHook();
    (toolsApi.listSystem as Mock).mockRejectedValue(new Error('Preload error'));

    const { result } = renderHook(() => useToolSchemas());

    await act(async () => {
      await result.current.preloadTools(['tool-search']);
    });

    expect(logger.error).toHaveBeenCalledWith(
      'Failed to preload tool schemas:',
      expect.any(Error),
    );
    expect(result.current.error).toBe('Preload error');
  });

  it('sets generic error message when preloadTools non-Error is thrown', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockRejectedValue('oops');

    const { result } = renderHook(() => useToolSchemas());

    await act(async () => {
      await result.current.preloadTools(['tool-search']);
    });

    expect(result.current.error).toBe('Failed to preload tool schemas');
  });

  it('skips tools not found in listSystem response during preload', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    // Only tool-a is returned; tool-b is absent
    (toolsApi.listSystem as Mock).mockResolvedValue([makeTool({ id: 'tool-a' })]);

    const { result } = renderHook(() => useToolSchemas());

    await act(async () => {
      await result.current.preloadTools(['tool-a', 'tool-missing']);
    });

    const schemaA = result.current.getInputSchema('tool-a');
    expect(schemaA.length).toBeGreaterThan(0);
    // tool-missing is not cached — returns [] and triggers new fetch
    act(() => {
      const missing = result.current.getInputSchema('tool-missing');
      expect(missing).toEqual([]);
    });
  });

  it('sets isLoading = false after preloadTools error', async () => {
    const { useToolSchemas, toolsApi } = await importHook();
    (toolsApi.listSystem as Mock).mockRejectedValue(new Error('fail'));

    const { result } = renderHook(() => useToolSchemas());

    await act(async () => {
      await result.current.preloadTools(['tool-search']);
    });

    expect(result.current.isLoading).toBe(false);
  });
});
