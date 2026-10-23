// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useUnresolvedRegion: membership and expanded/collapsed state of the
 * canvas's "Unresolved" region (see `utils/unresolvedRegion.ts`).
 *
 * Membership is recomputed when nodes are added, dropped, cleared, or have
 * their properties (`content`) replaced; position updates (drags, layouts)
 * do not touch it. The expanded state is the viewer's own and persists in
 * localStorage; with storage unavailable it starts expanded and toggles for
 * the session.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../types';
import {
  groupUnresolvedNodes,
  readUnresolvedRegionExpanded,
  regionCandidatesFromGraph,
  writeUnresolvedRegionExpanded,
  type UnresolvedRegion,
} from '../utils/unresolvedRegion';

interface NodeAttributesUpdatedPayload {
  type: string;
  name?: string;
}

interface UseUnresolvedRegionReturn {
  region: UnresolvedRegion;
  /** Collapse an expanded region or expand a collapsed one, and remember it. */
  toggle: () => void;
}

export function useUnresolvedRegion(
  graph: Graph<NodeAttributes, EdgeAttributes>,
): UseUnresolvedRegionReturn {
  const [expanded, setExpanded] = useState<boolean>(readUnresolvedRegionExpanded);
  const [membershipVersion, setMembershipVersion] = useState(0);

  useEffect(() => {
    const bump = () => setMembershipVersion((v) => v + 1);
    const onAttributes = (payload: NodeAttributesUpdatedPayload) => {
      if (payload.type !== 'set' || payload.name === 'content') bump();
    };
    graph.on('nodeAdded', bump);
    graph.on('nodeDropped', bump);
    graph.on('cleared', bump);
    graph.on('nodeAttributesUpdated', onAttributes);
    return () => {
      graph.off('nodeAdded', bump);
      graph.off('nodeDropped', bump);
      graph.off('cleared', bump);
      graph.off('nodeAttributesUpdated', onAttributes);
    };
  }, [graph]);

  const region = useMemo(
    () => groupUnresolvedNodes(regionCandidatesFromGraph(graph), expanded),
    // membershipVersion is the graph-change signal; graph itself is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [graph, expanded, membershipVersion],
  );

  const toggle = useCallback(() => {
    const next = !expanded;
    setExpanded(next);
    writeUnresolvedRegionExpanded(next);
  }, [expanded]);

  return { region, toggle };
}
