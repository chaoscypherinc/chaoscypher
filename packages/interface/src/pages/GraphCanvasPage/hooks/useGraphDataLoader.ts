// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useGraphDataLoader: Loads graph data from API into a graphology graph.
 *
 * Uses the bulk /graph/canvas endpoint to fetch all nodes, edges, and
 * templates in a single HTTP request instead of 40+ paginated calls.
 */

import { useState, useCallback, useRef } from 'react';
import type Graph from 'graphology';
import { graphApi } from '../../../services/api/graph';
import type { Template } from '../../../types';
import type { CanvasNode, CanvasEdge, CanvasTemplate } from '../../../types/graph';
import { filterKnowledgeNodes } from '../../../constants/templates';
import { populateGraphFromApi, applyDegreeSizing } from '../utils/transformers';
import type { NodeAttributes, EdgeAttributes, LayoutType } from '../types';
import { getApiErrorMessage } from '../../../utils/errors';
import { logger } from '../../../utils/logger';

/** Convert minimal canvas node to the shape populateGraphFromApi expects. */
function toApiNode(n: CanvasNode) {
  return {
    id: n.id,
    template_id: n.template_id,
    label: n.label,
    position: n.position ?? undefined,
    source_id: n.source_id ?? undefined,
    // Fields not included in canvas response — transformer handles undefined
    created_at: '',
    updated_at: '',
  };
}

/** Convert minimal canvas edge to the shape populateGraphFromApi expects. */
function toApiEdge(e: CanvasEdge) {
  return {
    id: e.id,
    template_id: e.template_id,
    source_node_id: e.source_node_id,
    target_node_id: e.target_node_id,
    label: e.label,
    created_at: '',
    updated_at: '',
  };
}

/** Convert minimal canvas template to the Template type. */
function toTemplate(t: CanvasTemplate): Template {
  return {
    id: t.id,
    name: t.name,
    template_type: t.template_type as 'node' | 'edge',
    description: t.description ?? undefined,
    icon: t.icon,
    color: t.color,
    properties: [],
    is_system: false,
    created_at: '',
    updated_at: '',
  };
}

interface UseGraphDataLoaderProps {
  graph: Graph<NodeAttributes, EdgeAttributes>;
  applyLayout: (type: LayoutType) => Promise<void>;
  layoutType: LayoutType;
  sourceIds?: string[];
}

export function useGraphDataLoader({
  graph,
  applyLayout,
  layoutType,
  sourceIds,
}: UseGraphDataLoaderProps) {
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasLoadedOnce = useRef(false);

  const loadGraphData = useCallback(async () => {
    try {
      // Initial load shows full spinner; subsequent loads use non-blocking indicator
      if (hasLoadedOnce.current) {
        setReloading(true);
      } else {
        setLoading(true);
      }
      setError(null);

      // Single bulk request replaces 3x fetchAllPages (40+ HTTP round-trips → 1)
      const canvasData = await graphApi.fetchCanvasData(sourceIds);

      // Build template lookup map for icon/color resolution
      const templateMap = new Map<string, Template>();
      for (const t of canvasData.templates) {
        templateMap.set(t.id, toTemplate(t));
      }

      const apiNodes = canvasData.nodes.map(toApiNode);
      const knowledgeNodes = filterKnowledgeNodes(apiNodes);

      // Load all nodes — sigma's WebGL renderer handles large graphs
      populateGraphFromApi(graph, knowledgeNodes, canvasData.edges.map(toApiEdge), templateMap);

      // Scale node sizes by connection count — hubs get larger, peripherals stay small
      applyDegreeSizing(graph);

      // Apply initial layout
      if (layoutType === 'mindmap' || layoutType === 'force') {
        await applyLayout(layoutType);
      }

      hasLoadedOnce.current = true;
      setLoading(false);
      setReloading(false);
    } catch (err) {
      const e = err as { name?: string; code?: string };
      if (e.name !== 'AbortError' && e.code !== 'ECONNABORTED') {
        logger.error('Error loading graph data:', err);
        setError(getApiErrorMessage(err) || 'Failed to load graph data');
      }
      setLoading(false);
      setReloading(false);
    }
  }, [graph, applyLayout, layoutType, sourceIds]);

  return {
    loading,
    reloading,
    error,
    setError,
    loadGraphData,
  };
}
