// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useUnresolvedBucket: keeps the synthetic "Unresolved (N)" hubs (one per
 * source document) in step with the graph, and holds which hubs show their
 * members.
 *
 * Membership is re-read when real nodes or document roots are added or
 * dropped, the graph is cleared, or a real node's properties (`content`) are
 * replaced; position updates (drags, layouts) and the hubs' own changes do
 * not touch it. After each change the hubs and their edges are synced into
 * the graph (`syncUnresolvedBuckets`). Every hub starts collapsed: only the
 * hub and its count show.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../types';
import { isUnresolvedBucketNode, isSyntheticNode } from '../types';
import {
  groupUnresolvedBySource,
  syncUnresolvedBuckets,
  unresolvedHiddenIds,
} from '../utils/unresolvedBucket';

interface NodeAttributesUpdatedPayload {
  key: string;
  type: string;
  name?: string;
}

interface UseUnresolvedBucketReturn {
  /** Every unresolved node, hub by hub. */
  memberIds: string[];
  /** Members by hub id (one hub per source, plus the no-source hub). */
  hubs: Map<string, string[]>;
  /** The hub each unresolved node hangs off. */
  hubOf: Map<string, string>;
  /** Whether a hub shows its members. */
  isExpanded: (hubId: string) => boolean;
  /** Members of the closed hubs, and the hubs of collapsed documents, to hide. */
  hiddenIds: Set<string>;
  /** Show a hub's members if hidden, hide them if shown (a double-click on it). */
  toggle: (hubId: string) => void;
  /** Show a hub's members (focusing one); the same state a double-click flips. */
  expand: (hubId: string) => void;
}

const NO_COLLAPSED_SOURCES: ReadonlySet<string> = new Set();

/**
 * @param graph - The canvas graph.
 * @param collapsedSources - Source ids whose document root is collapsed: their hubs hide too.
 */
export function useUnresolvedBucket(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  collapsedSources: ReadonlySet<string> = NO_COLLAPSED_SOURCES,
): UseUnresolvedBucketReturn {
  const [expandedHubs, setExpandedHubs] = useState<ReadonlySet<string>>(() => new Set());
  const [membershipVersion, setMembershipVersion] = useState(0);

  useEffect(() => {
    const bump = () => setMembershipVersion((v) => v + 1);
    // Real nodes and document roots change membership; the hubs themselves do not.
    const onNode = ({ key }: { key: string }) => {
      if (!isUnresolvedBucketNode(key)) bump();
    };
    const onAttributes = (payload: NodeAttributesUpdatedPayload) => {
      if (isSyntheticNode(payload.key)) return;
      if (payload.type !== 'set' || payload.name === 'content') bump();
    };
    graph.on('nodeAdded', onNode);
    graph.on('nodeDropped', onNode);
    graph.on('cleared', bump);
    graph.on('nodeAttributesUpdated', onAttributes);
    return () => {
      graph.off('nodeAdded', onNode);
      graph.off('nodeDropped', onNode);
      graph.off('cleared', bump);
      graph.off('nodeAttributesUpdated', onAttributes);
    };
  }, [graph]);

  const hubs = useMemo(
    () => groupUnresolvedBySource(graph),
    // membershipVersion is the graph-change signal; graph itself is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [graph, membershipVersion],
  );

  const memberIds = useMemo(() => [...hubs.values()].flat(), [hubs]);

  const hubOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const [hubId, memberIds] of hubs) for (const id of memberIds) map.set(id, hubId);
    return map;
  }, [hubs]);

  // The hubs live in the graph (an external system): sync them after each change.
  useEffect(() => {
    syncUnresolvedBuckets(graph);
  }, [graph, hubs]);

  const hiddenIds = useMemo(
    () => unresolvedHiddenIds(hubs, expandedHubs, collapsedSources),
    [hubs, expandedHubs, collapsedSources],
  );

  const isExpanded = useCallback((hubId: string) => expandedHubs.has(hubId), [expandedHubs]);

  const toggle = useCallback((hubId: string) => {
    setExpandedHubs((current) => {
      const next = new Set(current);
      if (next.has(hubId)) next.delete(hubId);
      else next.add(hubId);
      return next;
    });
  }, []);

  const expand = useCallback((hubId: string) => {
    setExpandedHubs((current) => (current.has(hubId) ? current : new Set(current).add(hubId)));
  }, []);

  return { memberIds, hubs, hubOf, isExpanded, hiddenIds, toggle, expand };
}
