// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useLayoutManager: Applies layout algorithms to the graphology graph.
 *
 * Reads nodes/edges from graph, computes positions, writes them back.
 * Synthetic nodes and edges never enter a layout. Pipeline-flagged nodes sit
 * out the chosen layout and are placed around their source document's
 * "Unresolved (N)" hub, set beside that document's root
 * (`utils/unresolvedBucket.ts`).
 */

import { useCallback } from 'react';
import { useSigma } from '@react-sigma/core';
import type Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes, LayoutType } from '../types';
import {
  SOURCE_GROUP_PREFIX,
  bucketSourceId,
  isSourceGroupNode,
  isSyntheticEdge,
  isSyntheticNode,
} from '../types';
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
import { groupUnresolvedBySource, layoutUnresolvedBuckets } from '../utils/unresolvedBucket';
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
    if (isSyntheticNode(id)) return; // Synthetic hubs excluded from layout
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
    if (isSyntheticEdge(id)) return; // Synthetic edges excluded from layout
    edges.push({ id, source, target });
  });
  return edges;
}

/**
 * Reposition source group nodes to the centroid of their member entities.
 * Called after layout algorithms reposition real nodes. Unresolved nodes
 * (`skip`) are left out: they are placed around their hub afterwards.
 */
function repositionSourceGroupNodes(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  skip: ReadonlySet<string> = new Set(),
): void {
  graph.forEachNode((nodeId, attrs) => {
    if (!isSourceGroupNode(nodeId)) return;

    const sourceId = attrs.sourceGroupId;
    if (!sourceId) return;

    let sumX = 0;
    let sumY = 0;
    let count = 0;

    graph.forEachNode((memberId, memberAttrs) => {
      if (skip.has(memberId)) return;
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

/**
 * Where each source's Unresolved hub goes: beside its document root, or,
 * before the roots load, beside the centroid of the document's laid-out
 * nodes. The no-source hub has no anchor.
 */
function hubAnchors(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  hubIds: Iterable<string>,
  laidOut: readonly LayoutNode[],
  positions: Map<string, { x: number; y: number }>,
): Map<string, { x: number; y: number }> {
  const anchors = new Map<string, { x: number; y: number }>();
  for (const hubId of hubIds) {
    const sourceId = bucketSourceId(hubId);
    if (!sourceId) continue;
    const rootId = `${SOURCE_GROUP_PREFIX}${sourceId}`;
    if (graph.hasNode(rootId)) {
      const root = graph.getNodeAttributes(rootId);
      anchors.set(hubId, { x: root.x, y: root.y });
      continue;
    }
    let sumX = 0;
    let sumY = 0;
    let count = 0;
    for (const node of laidOut) {
      const p = positions.get(node.id);
      if (node.sourceId !== sourceId || !p) continue;
      sumX += p.x;
      sumY += p.y;
      count++;
    }
    if (count > 0) anchors.set(hubId, { x: sumX / count, y: sumY / count });
  }
  return anchors;
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
        // The unresolved nodes are laid out around their source's hub, apart from the rest.
        const hubs = groupUnresolvedBySource(graph);
        const inBucket = new Set([...hubs.values()].flat());
        const nodes = extractNodes(graph).filter((n) => !inBucket.has(n.id));
        const edges = extractEdges(graph).filter(
          (e) => !inBucket.has(e.source) && !inBucket.has(e.target),
        );

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
        repositionSourceGroupNodes(graph, inBucket);
        const anchors = hubAnchors(graph, hubs.keys(), nodes, positions);
        applyPositionsToGraph(graph, layoutUnresolvedBuckets(hubs, anchors, positions.values()));
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
