// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../../services/api/client';
import {
  computeConstellationLayout,
  type GraphNode,
  type GraphEdge,
  type RawNode,
  type RawEdge,
} from '../../components/graphConstellation';

const NODES_QUERY_KEY = ['dashboard', 'graph', 'nodes'] as const;
const EDGES_QUERY_KEY = ['dashboard', 'graph', 'edges'] as const;

interface PaginatedNodesResponse {
  data: RawNode[];
  pagination?: { total: number };
}

interface PaginatedEdgesResponse {
  data: RawEdge[];
  pagination?: { total: number };
}

const MAX_RENDER_NODES = 195;
const CACHE_KEY = 'dashboard_graph_layout_v18';
/** Target layout extent (in layout units) — the draw layer multiplies these
 *  by `Math.min(w, h) / 600`, so e.g. 600 × 400 maps to the full min-axis of
 *  the canvas × two-thirds of it. Keeps the graph inside the viewport for
 *  any source/cluster count.
 */
const LAYOUT_TARGET_X = 600;
const LAYOUT_TARGET_Y = 400;

interface GraphDataResult {
  nodes: GraphNode[];
  edges: GraphEdge[];
  loading: boolean;
  totalNodes: number;
  totalEdges: number;
}

/**
 * Fetches graph data, samples if needed, runs the shared D3-force
 * constellation layout (with sessionStorage caching), and returns positioned
 * nodes + edges ready for Canvas rendering.
 *
 * The /nodes and /edges responses are cached via TanStack Query; the heavy
 * layout computation is memoised so it only re-runs when the raw data
 * changes. Returns empty arrays + `loading: false` on fetch failure (the
 * dashboard graph is decorative — a failed fetch must not break the page).
 */
export function useGraphData(): GraphDataResult {
  const nodesQuery = useQuery({
    queryKey: NODES_QUERY_KEY,
    queryFn: async (): Promise<PaginatedNodesResponse> => {
      const response = await apiClient.get<PaginatedNodesResponse>('/nodes', {
        params: { minimal: true, page_size: 400 },
      });
      return response.data;
    },
  });

  const edgesQuery = useQuery({
    queryKey: EDGES_QUERY_KEY,
    queryFn: async (): Promise<PaginatedEdgesResponse> => {
      const response = await apiClient.get<PaginatedEdgesResponse>('/edges', {
        params: { minimal: true, page_size: 800 },
      });
      return response.data;
    },
  });

  const loading = nodesQuery.isPending || edgesQuery.isPending;
  const failed = nodesQuery.isError || edgesQuery.isError;

  return useMemo<GraphDataResult>(() => {
    if (loading) {
      return { nodes: [], edges: [], loading: true, totalNodes: 0, totalEdges: 0 };
    }
    if (failed) {
      return { nodes: [], edges: [], loading: false, totalNodes: 0, totalEdges: 0 };
    }

    const allNodes: RawNode[] = nodesQuery.data?.data ?? [];
    const allEdges: RawEdge[] = edgesQuery.data?.data ?? [];
    const totalNodes = nodesQuery.data?.pagination?.total ?? allNodes.length;
    const totalEdges = edgesQuery.data?.pagination?.total ?? allEdges.length;

    if (allNodes.length === 0) {
      return { nodes: [], edges: [], loading: false, totalNodes: 0, totalEdges: 0 };
    }

    const { nodes, edges } = computeConstellationLayout(allNodes, allEdges, {
      maxRenderNodes: MAX_RENDER_NODES,
      cacheKey: CACHE_KEY,
      layoutTargetX: LAYOUT_TARGET_X,
      layoutTargetY: LAYOUT_TARGET_Y,
      dropOrphans: true,
      // Dashboard-only: settle nodes by their edges (organic, no spokes),
      // layer clusters in depth for real parallax, and pull sources in from
      // the corners. The per-source map opts into none of these.
      organicRelax: true,
      assignDepth: true,
      clusterSpread: 0.6,
    });

    return { nodes, edges, loading: false, totalNodes, totalEdges };
  }, [loading, failed, nodesQuery.data, edgesQuery.data]);
}
