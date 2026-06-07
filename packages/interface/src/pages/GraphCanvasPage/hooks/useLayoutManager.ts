// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useLayoutManager: Applies layout algorithms to the graphology graph.
 *
 * Reads nodes/edges from graph, computes positions, writes them back.
 */

import { useCallback } from 'react';
import { useSigma } from '@react-sigma/core';
import type Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes, LayoutType } from '../types';
import { isSourceGroupNode, isProvenanceEdge } from '../types';
import {
  applyForceLayout,
  applyGridLayout,
  applyMindmapLayout,
  applyHierarchicalLayout,
  applyRadialLayout,
  applyPositionsToGraph,
  type LayoutNode,
  type LayoutEdge,
} from '../utils/layoutUtils';
import { logger } from '../../../utils/logger';

interface UseLayoutManagerProps {
  graph: Graph<NodeAttributes, EdgeAttributes>;
  setLayoutType: (type: LayoutType) => void;
  setError: (error: string | null) => void;
}

/**
 * Extract LayoutNode[] from graphology graph.
 */
function extractNodes(graph: Graph<NodeAttributes, EdgeAttributes>): LayoutNode[] {
  const nodes: LayoutNode[] = [];
  graph.forEachNode((id, attrs) => {
    if (isSourceGroupNode(id)) return; // Virtual nodes excluded from layout
    nodes.push({ id, x: attrs.x, y: attrs.y, templateId: attrs.templateId, size: attrs.size, sourceId: attrs.sourceDocumentId });
  });
  return nodes;
}

/**
 * Extract LayoutEdge[] from graphology graph.
 */
function extractEdges(graph: Graph<NodeAttributes, EdgeAttributes>): LayoutEdge[] {
  const edges: LayoutEdge[] = [];
  graph.forEachEdge((id, _attrs, source, target) => {
    if (isProvenanceEdge(id)) return; // Virtual edges excluded from layout
    edges.push({ id, source, target });
  });
  return edges;
}

/**
 * Reposition source group nodes to the centroid of their member entities.
 * Called after layout algorithms reposition real nodes.
 */
function repositionSourceGroupNodes(graph: Graph<NodeAttributes, EdgeAttributes>): void {
  graph.forEachNode((nodeId, attrs) => {
    if (!isSourceGroupNode(nodeId)) return;

    const sourceId = attrs.sourceGroupId;
    if (!sourceId) return;

    let sumX = 0;
    let sumY = 0;
    let count = 0;

    graph.forEachNode((_memberId, memberAttrs) => {
      if (memberAttrs.sourceGroupMembership === sourceId) {
        sumX += memberAttrs.x || 0;
        sumY += memberAttrs.y || 0;
        count++;
      }
    });

    if (count > 0) {
      graph.setNodeAttribute(nodeId, 'x', sumX / count);
      graph.setNodeAttribute(nodeId, 'y', sumY / count);
    }
  });
}

export function useLayoutManager({
  graph,
  setLayoutType,
  setError,
}: UseLayoutManagerProps) {
  const sigma = useSigma<NodeAttributes, EdgeAttributes>();

  const applyLayout = useCallback(
    async (type: LayoutType) => {
      try {
        const nodes = extractNodes(graph);
        const edges = extractEdges(graph);

        let positions;

        switch (type) {
          case 'force':
            positions = applyForceLayout(nodes, edges);
            break;
          case 'grid':
            positions = applyGridLayout(nodes);
            break;
          case 'mindmap':
            positions = applyMindmapLayout(nodes, edges);
            break;
          case 'hierarchical':
            positions = applyHierarchicalLayout(nodes, edges);
            break;
          case 'radial':
            positions = applyRadialLayout(nodes, edges);
            break;
          default:
            // Manual: no layout change
            setLayoutType(type);
            return;
        }

        applyPositionsToGraph(graph, positions);
        repositionSourceGroupNodes(graph);
        setLayoutType(type);

        // Fit view after layout
        setTimeout(() => {
          sigma.getCamera().animatedReset({ duration: 400 });
        }, 50);
      } catch (error) {
        logger.error('Layout error:', error);
        setError('Failed to apply layout');
      }
    },
    [graph, sigma, setLayoutType, setError],
  );

  return { applyLayout };
}
