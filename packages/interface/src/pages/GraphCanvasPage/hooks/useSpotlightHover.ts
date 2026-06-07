// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useSpotlightHover: Debounced hover spotlight for sigma graph.
 *
 * Tracks the hovered node and its neighbors so reducers can dim
 * non-connected elements.  Activation is debounced to prevent flicker
 * when the mouse sweeps across many nodes; deactivation is immediate.
 */

import { useState, useEffect, useRef } from 'react';
import { useSigma } from '@react-sigma/core';
import type Graph from 'graphology';
import { useAppConfig } from '../../../contexts/useAppConfig';
import type { NodeAttributes, EdgeAttributes } from '../types';

interface UseSpotlightHoverProps {
  graph: Graph<NodeAttributes, EdgeAttributes>;
}

interface SpotlightHoverState {
  /** Currently hovered node, or null. */
  hoveredNode: string | null;
  /** Ref to the set of neighbor IDs for the hovered node. */
  hoveredNeighborsRef: React.RefObject<Set<string>>;
}

/**
 * Manage spotlight-hover state for sigma graph rendering.
 *
 * Registers enterNode / leaveNode listeners on the sigma instance.
 * Activation is debounced by `intervals_spotlight_hover_debounce_ms`
 * (operator-tunable) so rapid mouse movement doesn't flicker;
 * deactivation is immediate.
 */
export function useSpotlightHover({ graph }: UseSpotlightHoverProps): SpotlightHoverState {
  const sigma = useSigma<NodeAttributes, EdgeAttributes>();
  const config = useAppConfig();
  const SPOTLIGHT_HOVER_DELAY_MS = config.intervals_spotlight_hover_debounce_ms;
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const hoveredNeighborsRef = useRef<Set<string>>(new Set());
  const hoverActivationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const clearPendingActivation = () => {
      if (hoverActivationTimerRef.current !== null) {
        clearTimeout(hoverActivationTimerRef.current);
        hoverActivationTimerRef.current = null;
      }
    };

    const handleEnterNode = ({ node }: { node: string }) => {
      // Cancel any pending activation from a previous enterNode that
      // hasn't fired yet. The new node "wins".
      clearPendingActivation();
      hoverActivationTimerRef.current = setTimeout(() => {
        hoverActivationTimerRef.current = null;
        setHoveredNode(node);
        const neighbors = new Set<string>();
        graph.forEachNeighbor(node, (neighbor) => neighbors.add(neighbor));
        hoveredNeighborsRef.current = neighbors;
        sigma.refresh();
      }, SPOTLIGHT_HOVER_DELAY_MS);
    };

    const handleLeaveNode = () => {
      // If a debounce was pending, the spotlight never activated — just
      // cancel the timer and bail. No state change, no refresh, no flicker.
      if (hoverActivationTimerRef.current !== null) {
        clearPendingActivation();
        return;
      }
      // Otherwise the spotlight is currently active; clear it immediately.
      setHoveredNode(null);
      hoveredNeighborsRef.current = new Set();
      sigma.refresh();
    };

    sigma.on('enterNode', handleEnterNode);
    sigma.on('leaveNode', handleLeaveNode);
    return () => {
      clearPendingActivation();
      sigma.off('enterNode', handleEnterNode);
      sigma.off('leaveNode', handleLeaveNode);
    };
  }, [sigma, graph, SPOTLIGHT_HOVER_DELAY_MS]);

  return { hoveredNode, hoveredNeighborsRef };
}
