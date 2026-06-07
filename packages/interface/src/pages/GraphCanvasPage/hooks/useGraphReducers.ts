// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useGraphReducers: Sigma node and edge reducer functions.
 *
 * Applies visual effects — selection highlights, search dimming,
 * spotlight hover, gradient edges, zoom-adaptive icon visibility,
 * and provenance edge styling — via sigma's nodeReducer / edgeReducer
 * settings.
 */

import { useEffect } from 'react';
import { useSigma } from '@react-sigma/core';
import type Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../types';
import { isSourceGroupNode, isProvenanceEdge } from '../types';
import { GraphColors } from '../../../theme/colors';
import { ChaosCypherPalette } from '../../../theme/palette';

/** Minimum connections for a node to keep full template color intensity. */
const CONNECTION_THRESHOLD = 2;

/** Mix a hex color toward the dark background (#0A0E17) by a given intensity (0-1). */
function mixColorWithBackground(hex: string, intensity: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  // Background: #0A0E17
  const br = 10, bg = 14, bb = 23;
  const mr = Math.round(br + (r - br) * intensity);
  const mg = Math.round(bg + (g - bg) * intensity);
  const mb = Math.round(bb + (b - bb) * intensity);
  return `#${mr.toString(16).padStart(2, '0')}${mg.toString(16).padStart(2, '0')}${mb.toString(16).padStart(2, '0')}`;
}

interface UseGraphReducersProps {
  graph: Graph<NodeAttributes, EdgeAttributes>;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  highlightedNodeIds: Set<string>;
  hiddenNodeIds: Set<string>;
  hasActiveSearch: boolean;
  hoveredNode: string | null;
  hoveredNeighborsRef: React.RefObject<Set<string>>;
  iconVisibleBySizeRef: React.RefObject<Map<number, boolean>>;
  /** Source IDs that are currently collapsed — hides their provenance edges. */
  collapsedSourceIds?: Set<string>;
}

/**
 * Install node and edge reducers on the sigma instance.
 *
 * Reducers are re-installed whenever any of the visual-state inputs
 * change (selection, search, hover, zoom icons).
 */
export function useGraphReducers({
  graph,
  selectedNodeId,
  selectedEdgeId,
  highlightedNodeIds,
  hiddenNodeIds,
  hasActiveSearch,
  hoveredNode,
  hoveredNeighborsRef,
  iconVisibleBySizeRef,
  collapsedSourceIds,
}: UseGraphReducersProps): void {
  const sigma = useSigma<NodeAttributes, EdgeAttributes>();

  useEffect(() => {
    // Pre-compute selected node's neighbors so both reducers can
    // use them without re-traversing the graph per node/edge.
    const selectedNeighbors = new Set<string>();
    if (selectedNodeId && graph.hasNode(selectedNodeId)) {
      for (const neighbor of graph.neighbors(selectedNodeId)) {
        selectedNeighbors.add(neighbor);
      }
    }

    sigma.setSetting('nodeReducer', (node: string, data: NodeAttributes) => {
      const res: NodeAttributes & {
        highlighted?: boolean;
        hidden?: boolean;
        zIndex?: number;
        borderColor?: string;
        borderSize?: number;
        targetColor?: string;
      } = { ...data };

      // Zoom-adaptive icon visibility: if this is a pictogram node but the
      // camera-updated handler has decided its size class should currently
      // hide its icon, fall back to the default circle program.
      if (res.type === 'pictogram' && iconVisibleBySizeRef.current.get(res.size ?? 0) === false) {
        res.type = 'circle';
      }

      // Source group nodes: add glow border, skip other filters
      if (isSourceGroupNode(node)) {
        const sgColor = res.color || GraphColors.fadedFallback;
        res.borderColor = sgColor;
        res.borderSize = 0.5;
        return res;
      }

      // Template filter: hide nodes not in filter
      if (hiddenNodeIds.has(node)) {
        res.hidden = true;
        return res;
      }

      // Node glow: add a colored border halo matching the node's color
      const nodeColor = res.color || GraphColors.fadedFallback;
      res.borderColor = nodeColor;
      res.borderSize = 0.4;

      // Desaturate low-connection nodes — keeps their cluster hue but muted
      if (graph.degree(node) < CONNECTION_THRESHOLD) {
        res.color = mixColorWithBackground(nodeColor, 0.35);
        res.borderColor = mixColorWithBackground(nodeColor, 0.35);
      }

      // Selection spotlight: dim non-connected nodes, highlight neighbors
      if (selectedNodeId) {
        if (node === selectedNodeId) {
          res.highlighted = true;
          res.zIndex = 2;
          res.borderSize = 0.8;
        } else if (selectedNeighbors.has(node)) {
          res.zIndex = 1;
        } else {
          res.color = mixColorWithBackground(nodeColor, 0.25);
          res.borderColor = mixColorWithBackground(nodeColor, 0.15);
          res.label = '';
        }
      }

      // Search highlight
      if (hasActiveSearch) {
        if (highlightedNodeIds.has(node)) {
          res.highlighted = true;
          res.zIndex = 1;
        } else {
          res.color = mixColorWithBackground(res.color || GraphColors.fadedFallback, 0.2);
          res.borderColor = 'transparent';
          res.label = '';
        }
      }

      // Spotlight hover: dim non-connected nodes
      if (hoveredNode) {
        if (node === hoveredNode) {
          res.highlighted = true;
          res.zIndex = 2;
          res.borderSize = 0.8;
        } else if (hoveredNeighborsRef.current.has(node)) {
          res.zIndex = 1;
        } else {
          res.color = mixColorWithBackground(nodeColor, 0.25);
          res.borderColor = mixColorWithBackground(nodeColor, 0.15);
          res.label = '';
        }
      }

      return res;
    });

    sigma.setSetting('edgeReducer', (edge: string, data: EdgeAttributes) => {
      const res: EdgeAttributes & {
        hidden?: boolean;
        zIndex?: number;
        forceLabel?: boolean;
        targetColor?: string;
      } = { ...data };

      const source = graph.source(edge);
      const target = graph.target(edge);

      // Provenance edges: hide when source group is collapsed,
      // otherwise show as subtle gradient
      if (isProvenanceEdge(edge)) {
        // Check if either endpoint is in a collapsed group
        if (hiddenNodeIds.has(source) || hiddenNodeIds.has(target)) {
          res.hidden = true;
          return res;
        }
        res.type = 'line';
        const sourceIsLeaf = graph.degree(source) <= graph.degree(target);
        const provenanceBase = ChaosCypherPalette.primary;
        if (sourceIsLeaf) {
          res.color = mixColorWithBackground(provenanceBase, 0.18);
          res.targetColor = mixColorWithBackground(provenanceBase, 0.06);
        } else {
          res.color = mixColorWithBackground(provenanceBase, 0.06);
          res.targetColor = mixColorWithBackground(provenanceBase, 0.18);
        }
        return res;
      }

      // Hide edges connected to hidden nodes
      if (hiddenNodeIds.has(source) || hiddenNodeIds.has(target)) {
        res.hidden = true;
        return res;
      }

      // Gradient coloring — each end uses its own node's color.
      // Intensity scales inversely with that node's degree so hubs stay clean.
      const sourceDegree = graph.degree(source);
      const targetDegree = graph.degree(target);
      const sourceAttrs = graph.getNodeAttributes(source);
      const targetAttrs = graph.getNodeAttributes(target);
      const sourceColor = sourceAttrs?.color || GraphColors.fadedFallback;
      const targetColor = targetAttrs?.color || GraphColors.fadedFallback;

      const intensityForDegree = (deg: number) =>
        deg <= 10 ? 0.45
        : deg <= 30 ? 0.30
        : deg <= 100 ? 0.18
        : deg <= 300 ? 0.10
        : 0.06;

      res.color = mixColorWithBackground(sourceColor, intensityForDegree(sourceDegree));
      res.targetColor = mixColorWithBackground(targetColor, intensityForDegree(targetDegree));

      // Selection spotlight: highlight connected edges, dim everything else.
      // Uses the selected node's own color for a clean radial burst.
      if (selectedNodeId && selectedNeighbors.size > 0) {
        if (source === selectedNodeId || target === selectedNodeId) {
          const selectedAttrs = graph.getNodeAttributes(selectedNodeId);
          const selectedColor = selectedAttrs?.color || GraphColors.fadedFallback;
          const selectedDegree = graph.degree(selectedNodeId);
          if (selectedDegree > 30) {
            const hubIntensity = selectedDegree > 100 ? 0.5 : 0.6;
            res.color = mixColorWithBackground(selectedColor, hubIntensity);
            res.targetColor = mixColorWithBackground(selectedColor, hubIntensity);
            res.size = 1;
            res.label = '';
          } else {
            res.color = selectedColor;
            res.targetColor = selectedColor;
            res.size = 2;
          }
          res.zIndex = 1;
        } else {
          // Dim non-connected edges
          res.color = mixColorWithBackground(sourceColor, 0.06);
          res.targetColor = mixColorWithBackground(targetColor, 0.06);
          res.label = '';
          res.zIndex = -1;
        }
      }

      // Selected edge
      if (edge === selectedEdgeId) {
        res.size = 4;
        res.zIndex = 2;
      }

      // Search: fade edges not connected to matched nodes
      if (hasActiveSearch) {
        if (!highlightedNodeIds.has(source) && !highlightedNodeIds.has(target)) {
          res.color = mixColorWithBackground(sourceColor, 0.05);
          res.targetColor = mixColorWithBackground(targetColor, 0.05);
          res.label = '';
        }
      }

      // Spotlight hover: highlight connected, dim everything else.
      // Uses hovered node's color for consistent radial burst.
      if (hoveredNode) {
        if (source === hoveredNode || target === hoveredNode) {
          const hoverAttrs = graph.getNodeAttributes(hoveredNode);
          const hoverColor = hoverAttrs?.color || GraphColors.fadedFallback;
          const hoverDegree = graph.degree(hoveredNode);
          if (hoverDegree > 30) {
            const hoverIntensity = hoverDegree > 100 ? 0.5 : 0.6;
            res.color = mixColorWithBackground(hoverColor, hoverIntensity);
            res.targetColor = mixColorWithBackground(hoverColor, hoverIntensity);
            res.size = 1;
            res.label = '';
          } else {
            res.color = hoverColor;
            res.targetColor = hoverColor;
            res.size = 2;
            res.forceLabel = true;
          }
          res.zIndex = 2;
        } else {
          res.color = mixColorWithBackground(sourceColor, 0.06);
          res.targetColor = mixColorWithBackground(targetColor, 0.06);
          res.label = '';
          res.zIndex = -1;
        }
      }

      return res;
    });
  }, [sigma, graph, selectedNodeId, selectedEdgeId, highlightedNodeIds, hiddenNodeIds, hasActiveSearch, hoveredNode, hoveredNeighborsRef, iconVisibleBySizeRef, collapsedSourceIds]);
}
