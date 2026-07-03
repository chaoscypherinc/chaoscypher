// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Data hook for the per-source Knowledge map on the Overview tab.
 *
 * Fetches just this source's subgraph via the bulk canvas endpoint
 * (`/graph/canvas?source_ids=[id]`), then runs the shared constellation
 * layout to produce positioned nodes/edges for the rotating-glow preview.
 * Orphan nodes are kept (unlike the dashboard) so the map faithfully reflects
 * the source's entities, and the layout is cached per source.
 *
 * The preview is decorative — a failed fetch resolves to `isEmpty` so the
 * card simply doesn't render rather than surfacing an error.
 */

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { graphApi } from '../../../../../services/api/graph';
import { getColorForTemplate } from '../../../../../utils/colorUtils';
import {
  computeConstellationLayout,
  type GraphNode,
  type GraphEdge,
} from '../../../../../components/graphConstellation';
import { useOverviewData } from './useOverviewData';

/** Above this, a connected BFS sample keeps the preview smooth. */
const MAX_PREVIEW_NODES = 200;

export interface SourceGraphPreviewData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  entityCount: number;
  relationshipCount: number;
  loading: boolean;
  /** True when there is nothing meaningful to render (no nodes, or fetch failed). */
  isEmpty: boolean;
}

const EMPTY: SourceGraphPreviewData = {
  nodes: [],
  edges: [],
  entityCount: 0,
  relationshipCount: 0,
  loading: false,
  isEmpty: true,
};

export function useSourceGraphPreview(
  sourceId: string,
  enabled: boolean,
): SourceGraphPreviewData {
  const query = useQuery({
    queryKey: ['graph', 'canvas', 'source', sourceId] as const,
    queryFn: () => graphApi.fetchCanvasData([sourceId]),
    enabled,
  });

  const loading = enabled && query.isPending;
  const failed = query.isError;
  const data = query.data;

  // Colour nodes by their template's real colour so the map matches the
  // Overview distribution charts (which use the same source). Mirrors the
  // chart's `tpl.color || getColorForTemplate(tpl.id)` rule; unknown template
  // ids still resolve to a stable themed swatch. Shares the templates query
  // with the charts' `useOverviewData`, so this adds no extra fetch.
  const { templateList } = useOverviewData(sourceId);
  const resolveColor = useMemo(() => {
    const byId = new Map(
      templateList.map((t) => [t.id, t.color || getColorForTemplate(t.id)]),
    );
    return (templateId: string) => byId.get(templateId) ?? getColorForTemplate(templateId);
  }, [templateList]);

  return useMemo<SourceGraphPreviewData>(() => {
    if (!enabled || loading) {
      return { ...EMPTY, loading, isEmpty: false };
    }
    if (failed || !data) {
      return EMPTY;
    }

    const entityCount = data.total_nodes;
    const relationshipCount = data.total_edges;

    if (data.nodes.length === 0) {
      return { ...EMPTY, entityCount, relationshipCount };
    }

    const rawNodes = data.nodes.map((n) => ({
      id: n.id,
      template_id: n.template_id,
      source_id: n.source_id ?? undefined,
    }));
    const rawEdges = data.edges.map((e) => ({
      source_node_id: e.source_node_id,
      target_node_id: e.target_node_id,
    }));

    const { nodes, edges } = computeConstellationLayout(rawNodes, rawEdges, {
      maxRenderNodes: MAX_PREVIEW_NODES,
      cacheKey: `source_graph_layout_${sourceId}_v1`,
      dropOrphans: false,
      resolveColor,
    });

    return {
      nodes,
      edges,
      entityCount,
      relationshipCount,
      loading: false,
      isEmpty: nodes.length === 0,
    };
  }, [enabled, loading, failed, data, sourceId, resolveColor]);
}
