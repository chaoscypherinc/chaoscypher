// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useFocusNode: the canvas's one "focus node" path.
 *
 * Selecting a node by id (a search's chosen match, a link in the properties
 * panel) goes through here. A node hidden inside a collapsed Unresolved
 * hub opens its own hub first (the same state a double-click on that hub
 * flips, so a later double-click closes it again); then the node is
 * selected and the camera centres on it. An ordinary node leaves the hubs
 * as they are.
 */

import { useCallback } from 'react';
import { useSigma } from '@react-sigma/core';
import type Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes, GraphNodeData } from '../types';

interface UseFocusNodeProps {
  graph: Graph<NodeAttributes, EdgeAttributes>;
  /** The Unresolved hubs' state (`useUnresolvedBucket`). */
  bucket: { hiddenIds: Set<string>; hubOf: Map<string, string>; expand: (hubId: string) => void };
  /** Select the node (`useGraphSelection.handleNodeClick`). */
  onSelect: (nodeId: string, data: GraphNodeData) => void;
}

const FOCUS_DURATION_MS = 400;

export function useFocusNode({ graph, bucket, onSelect }: UseFocusNodeProps): (nodeId: string) => void {
  const sigma = useSigma<NodeAttributes, EdgeAttributes>();
  const { hiddenIds, hubOf, expand } = bucket;

  return useCallback(
    (nodeId: string) => {
      if (!graph.hasNode(nodeId)) return;
      const hubId = hubOf.get(nodeId);
      if (hubId && hiddenIds.has(nodeId)) expand(hubId);
      onSelect(nodeId, graph.getNodeAttributes(nodeId));
      const display = sigma.getNodeDisplayData(nodeId);
      if (display) {
        sigma.getCamera().animate({ x: display.x, y: display.y }, { duration: FOCUS_DURATION_MS });
      }
    },
    [graph, sigma, hiddenIds, hubOf, expand, onSelect],
  );
}
