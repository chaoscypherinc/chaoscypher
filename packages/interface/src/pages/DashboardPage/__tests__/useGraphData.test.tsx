// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for ``useGraphData`` — the DashboardPage decorative-graph hook.
 *
 * The hook fans out to two TanStack Query reads (``/nodes`` and ``/edges``
 * via ``apiClient.get``), then runs a deterministic-ish pipeline in a
 * ``useMemo``: connected-cluster sampling, orphan dropping, D3-force mindmap
 * layout (clustered by source then template), bounding-box normalization,
 * sessionStorage layout caching, and a colour/size/opacity transform into
 * ``GraphNode`` / ``GraphEdge`` shapes.
 *
 * We mock only the API client (resolving synthetic payloads). d3-force and
 * the palette are exercised for real so the transform breadth is covered.
 * ``Math.random`` is stubbed where determinism matters so assertions about
 * which nodes survive the sampler are stable.
 */

import React from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { ChaosCypherPalette } from '../../../theme/palette';
import type { ApiResponse, RequestConfig } from '../../../services/api/client';

// ── API client mock ──────────────────────────────────────────────────────
// The real client exposes `apiClient.get<T>(url, config) => Promise<{ data }>`.
// The hook's queryFn unwraps `.data`, so the mock must resolve that wrapper.
const getMock =
  vi.fn<(url: string, config?: RequestConfig) => Promise<ApiResponse>>();

vi.mock('../../../services/api/client', () => ({
  apiClient: {
    get: (url: string, config?: RequestConfig) => getMock(url, config),
  },
}));

import { useGraphData } from '../useGraphData';

// ── Synthetic payload helpers ────────────────────────────────────────────

interface RawNodePayload {
  id: string;
  template_id?: string;
  source_id?: string;
}
interface RawEdgePayload {
  source_node_id: string;
  target_node_id: string;
}

function nodesResponse(
  data: RawNodePayload[],
  total?: number,
): ApiResponse {
  return {
    data: {
      data,
      pagination: total !== undefined ? { total } : undefined,
    },
    status: 200,
    headers: new Headers(),
  };
}

function edgesResponse(
  data: RawEdgePayload[],
  total?: number,
): ApiResponse {
  return {
    data: {
      data,
      pagination: total !== undefined ? { total } : undefined,
    },
    status: 200,
    headers: new Headers(),
  };
}

/**
 * Route the mocked `apiClient.get` to the right payload based on URL so a
 * single mock serves both `/nodes` and `/edges` queries.
 */
function wireGraph(
  nodes: ApiResponse | (() => Promise<ApiResponse>),
  edges: ApiResponse | (() => Promise<ApiResponse>),
): void {
  getMock.mockImplementation((url: string) => {
    if (url === '/nodes') {
      return typeof nodes === 'function' ? nodes() : Promise.resolve(nodes);
    }
    if (url === '/edges') {
      return typeof edges === 'function' ? edges() : Promise.resolve(edges);
    }
    return Promise.reject(new Error(`unexpected url: ${url}`));
  });
}

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

const PALETTE_VALUES = Object.values(ChaosCypherPalette) as string[];

beforeEach(() => {
  getMock.mockReset();
  // sessionStorage is shared across tests in jsdom — clear so each test
  // starts on a cache miss (and exercises the layout path) by default.
  sessionStorage.clear();
  // Deterministic shuffles/jitter so sampler output is stable for assertions.
  vi.spyOn(Math, 'random').mockReturnValue(0.42);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useGraphData', () => {
  it('returns loading state while queries are pending, then resolves', async () => {
    wireGraph(
      nodesResponse(
        [
          { id: 'a', template_id: 't1', source_id: 's1' },
          { id: 'b', template_id: 't1', source_id: 's1' },
        ],
        2,
      ),
      edgesResponse([{ source_node_id: 'a', target_node_id: 'b' }], 1),
    );

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });

    // First synchronous render: both queries pending → loading true.
    expect(result.current.loading).toBe(true);
    expect(result.current.nodes).toEqual([]);
    expect(result.current.edges).toEqual([]);

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.nodes).toHaveLength(2);
    expect(result.current.edges).toHaveLength(1);
  });

  it('transforms nodes with finite coords, a palette colour, and positive size', async () => {
    wireGraph(
      nodesResponse([
        { id: 'a', template_id: 't1', source_id: 's1' },
        { id: 'b', template_id: 't2', source_id: 's1' },
        { id: 'c', template_id: 't2', source_id: 's2' },
      ]),
      edgesResponse([
        { source_node_id: 'a', target_node_id: 'b' },
        { source_node_id: 'b', target_node_id: 'c' },
      ]),
    );

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.nodes).toHaveLength(3);
    for (const node of result.current.nodes) {
      expect(Number.isFinite(node.x)).toBe(true);
      expect(Number.isFinite(node.y)).toBe(true);
      expect(node.radius).toBeGreaterThan(0);
      expect(node.opacity).toBeGreaterThan(0);
      expect(PALETTE_VALUES).toContain(node.color);
    }
  });

  it('colours nodes deterministically by template_id (same template → same colour)', async () => {
    wireGraph(
      nodesResponse([
        { id: 'a', template_id: 'shared', source_id: 's1' },
        { id: 'b', template_id: 'shared', source_id: 's1' },
        { id: 'c', template_id: 'other', source_id: 's1' },
      ]),
      edgesResponse([
        { source_node_id: 'a', target_node_id: 'b' },
        { source_node_id: 'b', target_node_id: 'c' },
      ]),
    );

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    const byId = new Map(result.current.nodes.map((n) => [n.id, n]));
    expect(byId.get('a')!.color).toBe(byId.get('b')!.color);
  });

  it('maps edges to source/target/color/opacity with endpoints present in node set', async () => {
    wireGraph(
      nodesResponse([
        { id: 'a', template_id: 't1', source_id: 's1' },
        { id: 'b', template_id: 't1', source_id: 's1' },
        { id: 'c', template_id: 't1', source_id: 's1' },
      ]),
      edgesResponse([
        { source_node_id: 'a', target_node_id: 'b' },
        { source_node_id: 'b', target_node_id: 'c' },
      ]),
    );

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    const nodeIds = new Set(result.current.nodes.map((n) => n.id));
    expect(result.current.edges.length).toBeGreaterThan(0);
    for (const edge of result.current.edges) {
      expect(nodeIds.has(edge.source)).toBe(true);
      expect(nodeIds.has(edge.target)).toBe(true);
      expect(typeof edge.color).toBe('string');
      expect(edge.color.startsWith('#')).toBe(true);
      // opacity = 0.03 + fade^2 * 0.15 → within (0, 0.18].
      expect(edge.opacity).toBeGreaterThan(0);
      expect(edge.opacity).toBeLessThanOrEqual(0.18 + 1e-9);
    }
  });

  it('uses pagination.total for totalNodes / totalEdges when provided', async () => {
    wireGraph(
      nodesResponse(
        [
          { id: 'a', template_id: 't1', source_id: 's1' },
          { id: 'b', template_id: 't1', source_id: 's1' },
        ],
        999,
      ),
      edgesResponse([{ source_node_id: 'a', target_node_id: 'b' }], 555),
    );

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.totalNodes).toBe(999);
    expect(result.current.totalEdges).toBe(555);
  });

  it('falls back to array lengths when pagination is absent', async () => {
    wireGraph(
      nodesResponse([
        { id: 'a', template_id: 't1', source_id: 's1' },
        { id: 'b', template_id: 't1', source_id: 's1' },
      ]),
      edgesResponse([{ source_node_id: 'a', target_node_id: 'b' }]),
    );

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.totalNodes).toBe(2);
    expect(result.current.totalEdges).toBe(1);
  });

  it('drops orphan nodes (no edges) from the rendered set', async () => {
    wireGraph(
      nodesResponse([
        { id: 'a', template_id: 't1', source_id: 's1' },
        { id: 'b', template_id: 't1', source_id: 's1' },
        { id: 'orphan', template_id: 't2', source_id: 's2' },
      ]),
      edgesResponse([{ source_node_id: 'a', target_node_id: 'b' }]),
    );

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    const ids = result.current.nodes.map((n) => n.id);
    expect(ids).toContain('a');
    expect(ids).toContain('b');
    expect(ids).not.toContain('orphan');
    // totalNodes still reflects the full set length (3), not the rendered 2.
    expect(result.current.totalNodes).toBe(3);
  });

  it('returns empty result for an empty node payload', async () => {
    wireGraph(nodesResponse([]), edgesResponse([]));

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.nodes).toEqual([]);
    expect(result.current.edges).toEqual([]);
    expect(result.current.totalNodes).toBe(0);
    expect(result.current.totalEdges).toBe(0);
  });

  it('returns empty arrays (loading:false) when a fetch rejects', async () => {
    wireGraph(
      () => Promise.reject(new Error('boom')),
      edgesResponse([]),
    );

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.nodes).toEqual([]);
    expect(result.current.edges).toEqual([]);
    expect(result.current.totalNodes).toBe(0);
    expect(result.current.totalEdges).toBe(0);
  });

  it('issues the /nodes and /edges requests with minimal+page_size params', async () => {
    wireGraph(
      nodesResponse([{ id: 'a', template_id: 't1', source_id: 's1' }]),
      edgesResponse([]),
    );

    renderHook(() => useGraphData(), { wrapper: makeWrapper() });

    await waitFor(() =>
      expect(getMock).toHaveBeenCalledWith('/nodes', {
        params: { minimal: true, page_size: 400 },
      }),
    );
    expect(getMock).toHaveBeenCalledWith('/edges', {
      params: { minimal: true, page_size: 800 },
    });
  });

  it('samples down to the render cap while preserving connectivity', async () => {
    // Build a connected chain far larger than MAX_RENDER_NODES (195) so the
    // BFS sampler (sampleConnected) is exercised and the result is capped.
    const N = 260;
    const nodes: RawNodePayload[] = Array.from({ length: N }, (_, i) => ({
      id: `n${i}`,
      template_id: `tpl${i % 4}`,
      source_id: `src${i % 3}`,
    }));
    const edges: RawEdgePayload[] = Array.from({ length: N - 1 }, (_, i) => ({
      source_node_id: `n${i}`,
      target_node_id: `n${i + 1}`,
    }));

    wireGraph(nodesResponse(nodes, N), edgesResponse(edges, N - 1));

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    // Rendered node set is bounded by the sampler cap (195).
    expect(result.current.nodes.length).toBeGreaterThan(0);
    expect(result.current.nodes.length).toBeLessThanOrEqual(195);
    // Counts still report the unsampled totals from pagination.
    expect(result.current.totalNodes).toBe(N);
    expect(result.current.totalEdges).toBe(N - 1);
    // Every rendered node has finite coordinates after layout+normalize.
    for (const node of result.current.nodes) {
      expect(Number.isFinite(node.x)).toBe(true);
      expect(Number.isFinite(node.y)).toBe(true);
    }
  });

  it('reuses a cached sessionStorage layout on the second identical load (cache hit path)', async () => {
    const payloadNodes: RawNodePayload[] = [
      { id: 'a', template_id: 't1', source_id: 's1' },
      { id: 'b', template_id: 't1', source_id: 's1' },
    ];
    const payloadEdges: RawEdgePayload[] = [
      { source_node_id: 'a', target_node_id: 'b' },
    ];
    wireGraph(nodesResponse(payloadNodes), edgesResponse(payloadEdges));

    // First render: cache miss → runs layout, then writes sessionStorage.
    const first = renderHook(() => useGraphData(), { wrapper: makeWrapper() });
    await waitFor(() => expect(first.result.current.loading).toBe(false));

    const stored = sessionStorage.getItem('dashboard_graph_layout_v18');
    expect(stored).not.toBeNull();
    const firstNodes = first.result.current.nodes.map((n) => ({
      id: n.id,
      x: n.x,
      y: n.y,
    }));

    // Second render with the SAME data → same cache version → cache hit path,
    // applying stored positions verbatim.
    const second = renderHook(() => useGraphData(), { wrapper: makeWrapper() });
    await waitFor(() => expect(second.result.current.loading).toBe(false));

    const secondNodes = second.result.current.nodes.map((n) => ({
      id: n.id,
      x: n.x,
      y: n.y,
    }));
    expect(secondNodes).toEqual(firstNodes);
  });

  it('tolerates inter-source and inter-template edges (multi-cluster layout)', async () => {
    // Two sources, multiple templates within each, plus cross-source edges so
    // applyMindmapLayout exercises both the source-level and template-level
    // force passes and the inter-cluster edge construction.
    const nodes: RawNodePayload[] = [
      { id: 'a1', template_id: 'tA', source_id: 'S1' },
      { id: 'a2', template_id: 'tB', source_id: 'S1' },
      { id: 'a3', template_id: 'tB', source_id: 'S1' },
      { id: 'b1', template_id: 'tA', source_id: 'S2' },
      { id: 'b2', template_id: 'tC', source_id: 'S2' },
    ];
    const edges: RawEdgePayload[] = [
      { source_node_id: 'a1', target_node_id: 'a2' }, // same source, diff template
      { source_node_id: 'a2', target_node_id: 'a3' }, // same source+template
      { source_node_id: 'a1', target_node_id: 'b1' }, // cross-source
      { source_node_id: 'b1', target_node_id: 'b2' }, // same source, diff template
    ];
    wireGraph(nodesResponse(nodes), edgesResponse(edges));

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.nodes).toHaveLength(5);
    expect(result.current.edges).toHaveLength(4);
    for (const node of result.current.nodes) {
      expect(Number.isFinite(node.x)).toBe(true);
      expect(Number.isFinite(node.y)).toBe(true);
    }
  });

  it('falls back to node id for templateId/sourceId when fields are absent', async () => {
    // No template_id / source_id → hook substitutes the node id (templateId)
    // and '_none' (sourceId). Still must produce a valid coloured node.
    wireGraph(
      nodesResponse([
        { id: 'lone1' },
        { id: 'lone2' },
      ]),
      edgesResponse([{ source_node_id: 'lone1', target_node_id: 'lone2' }]),
    );

    const { result } = renderHook(() => useGraphData(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.nodes).toHaveLength(2);
    for (const node of result.current.nodes) {
      expect(PALETTE_VALUES).toContain(node.color);
      expect(Number.isFinite(node.x)).toBe(true);
    }
  });
});
