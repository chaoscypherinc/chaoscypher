// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useNodeDrag: Implements node dragging for Sigma.js v3.
 *
 * Sigma doesn't have built-in drag support. This hook handles:
 * - downNode: record drag target, disable camera
 * - mousemove: update node x/y via graph.setNodeAttribute()
 * - mouseup: release, re-enable camera
 *
 * For source group nodes, dragging moves all member entities as a unit.
 */

import { useEffect, useRef } from 'react';
import { useSigma } from '@react-sigma/core';
import type { SigmaNodeEventPayload } from 'sigma/types';
import type { NodeAttributes, EdgeAttributes } from '../types';
import { isSourceGroupNode, SOURCE_GROUP_PREFIX } from '../types';

export function useNodeDrag() {
  const sigma = useSigma<NodeAttributes, EdgeAttributes>();
  const dragStateRef = useRef<{
    dragging: boolean;
    draggedNode: string | null;
    prevX: number;
    prevY: number;
  }>({
    dragging: false,
    draggedNode: null,
    prevX: 0,
    prevY: 0,
  });

  useEffect(() => {
    const graph = sigma.getGraph();
    const camera = sigma.getCamera();

    const handleDownNode = (payload: SigmaNodeEventPayload) => {
      const attrs = graph.getNodeAttributes(payload.node);
      dragStateRef.current = {
        dragging: true,
        draggedNode: payload.node,
        prevX: attrs.x,
        prevY: attrs.y,
      };
      camera.disable();
      payload.preventSigmaDefault();
    };

    const handleMouseMove = (event: MouseEvent) => {
      const state = dragStateRef.current;
      if (!state.dragging || !state.draggedNode) return;

      // Convert viewport coords to graph coords
      const container = sigma.getContainer();
      const rect = container.getBoundingClientRect();
      const pos = sigma.viewportToGraph({
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });

      // Update dragged node position
      graph.setNodeAttribute(state.draggedNode, 'x', pos.x);
      graph.setNodeAttribute(state.draggedNode, 'y', pos.y);

      // Group drag: move all member entities by the same delta
      if (isSourceGroupNode(state.draggedNode)) {
        const dx = pos.x - state.prevX;
        const dy = pos.y - state.prevY;
        const sourceId = state.draggedNode.slice(SOURCE_GROUP_PREFIX.length);

        graph.forEachNode((nodeId, attrs) => {
          if (nodeId !== state.draggedNode && attrs.sourceGroupMembership === sourceId) {
            graph.setNodeAttribute(nodeId, 'x', (attrs.x || 0) + dx);
            graph.setNodeAttribute(nodeId, 'y', (attrs.y || 0) + dy);
          }
        });
      }

      state.prevX = pos.x;
      state.prevY = pos.y;
    };

    const handleMouseUp = () => {
      const state = dragStateRef.current;
      if (state.dragging) {
        state.dragging = false;
        state.draggedNode = null;
        // Brief delay before re-enabling camera to prevent momentum jump
        requestAnimationFrame(() => camera.enable());
      }
    };

    sigma.on('downNode', handleDownNode);
    const container = sigma.getContainer();
    container.addEventListener('mousemove', handleMouseMove);
    container.addEventListener('mouseup', handleMouseUp);
    container.addEventListener('mouseleave', handleMouseUp);

    return () => {
      sigma.off('downNode', handleDownNode);
      container.removeEventListener('mousemove', handleMouseMove);
      container.removeEventListener('mouseup', handleMouseUp);
      container.removeEventListener('mouseleave', handleMouseUp);
    };
  }, [sigma]);
}
