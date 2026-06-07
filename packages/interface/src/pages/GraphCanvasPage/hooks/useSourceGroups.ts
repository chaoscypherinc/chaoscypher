// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Source group state management for the graph canvas.
 *
 * Manages virtual source group nodes: loading from API, expand/collapse
 * behavior, external edge detection, and visibility toggling.
 *
 * Visibility is driven by the node/edge reducers (not graph mutations)
 * so toggling is O(1) regardless of member count.
 */

import { useCallback, useMemo, useRef, useState } from 'react';
import type Graph from 'graphology';
import type { SourceGroup } from '../../../types/graph';
import { graphApi } from '../../../services/api/graph';
import { logger } from '../../../utils/logger';
import {
  addSourceGroupNode,
  addProvenanceEdges,
} from '../utils/transformers';
import type { NodeAttributes, EdgeAttributes } from '../types';
import { SOURCE_GROUP_PREFIX, isSourceGroupNode } from '../types';

export interface SourceGroupState {
  group: SourceGroup;
  memberNodeIds: string[];
  externalNodeIds: Set<string>;
  expanded: boolean;
}

interface UseSourceGroupsReturn {
  groups: Map<string, SourceGroupState>;
  /** Set of node IDs that should be hidden due to collapsed source groups. */
  collapsedMemberIds: Set<string>;
  /** Set of source IDs that are currently collapsed. */
  collapsedSourceIds: Set<string>;
  loadSourceGroups: (graph: Graph<NodeAttributes, EdgeAttributes>) => Promise<void>;
  toggleGroup: (graph: Graph<NodeAttributes, EdgeAttributes>, sourceId: string) => void;
  expandAll: (graph: Graph<NodeAttributes, EdgeAttributes>) => void;
  collapseAll: (graph: Graph<NodeAttributes, EdgeAttributes>) => void;
  getNodeSourceGroup: (nodeId: string) => SourceGroupState | undefined;
  loading: boolean;
}

/**
 * Detect which member nodes have edges connecting to nodes outside the group.
 */
function detectExternalNodes(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  memberNodeIds: string[],
): Set<string> {
  const memberSet = new Set(memberNodeIds);
  const external = new Set<string>();

  for (const nodeId of memberNodeIds) {
    if (!graph.hasNode(nodeId)) continue;

    const neighbors = graph.neighbors(nodeId);
    for (const neighbor of neighbors) {
      if (memberSet.has(neighbor) || isSourceGroupNode(neighbor)) continue;
      external.add(nodeId);
      break;
    }
  }

  return external;
}

export function useSourceGroups(): UseSourceGroupsReturn {
  const [groups, setGroups] = useState<Map<string, SourceGroupState>>(new Map());
  const [loading, setLoading] = useState(false);
  const groupsRef = useRef(groups);
  groupsRef.current = groups;

  // Compute collapsed member IDs from state — drives reducer visibility
  const collapsedMemberIds = useMemo(() => {
    const ids = new Set<string>();
    for (const state of groups.values()) {
      if (!state.expanded) {
        for (const nodeId of state.memberNodeIds) {
          ids.add(nodeId);
        }
      }
    }
    return ids;
  }, [groups]);

  // Compute collapsed source IDs — drives provenance edge hiding in reducer
  const collapsedSourceIds = useMemo(() => {
    const ids = new Set<string>();
    for (const [sourceId, state] of groups) {
      if (!state.expanded) ids.add(sourceId);
    }
    return ids;
  }, [groups]);

  const loadSourceGroups = useCallback(async (graph: Graph<NodeAttributes, EdgeAttributes>) => {
    setLoading(true);
    try {
      const response = await graphApi.fetchSourceGroups();
      const newGroups = new Map<string, SourceGroupState>();

      for (const group of response.groups) {
        const memberNodeIds = addSourceGroupNode(graph, group);
        if (memberNodeIds.length === 0) continue;

        const externalNodeIds = detectExternalNodes(graph, memberNodeIds);

        const state: SourceGroupState = {
          group,
          memberNodeIds,
          externalNodeIds,
          expanded: true,
        };

        newGroups.set(group.source_id, state);

        // Create provenance edges once — never removed, visibility via reducer
        addProvenanceEdges(graph, group.source_id, memberNodeIds);
      }

      setGroups(newGroups);
    } catch (err) {
      logger.error('Failed to load source groups:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleGroup = useCallback(
    (graph: Graph<NodeAttributes, EdgeAttributes>, sourceId: string) => {
      const state = groupsRef.current.get(sourceId);
      if (!state) return;

      const newState = { ...state, expanded: !state.expanded };

      // Update the group node label to show count when collapsed
      const groupNodeId = `${SOURCE_GROUP_PREFIX}${sourceId}`;
      if (graph.hasNode(groupNodeId)) {
        graph.setNodeAttribute(
          groupNodeId,
          'label',
          newState.expanded
            ? state.group.title
            : `${state.group.title} (${state.memberNodeIds.length})`,
        );
      }

      // Flip state — the reducer handles visibility, no graph mutations needed
      const newGroups = new Map(groupsRef.current);
      newGroups.set(sourceId, newState);
      setGroups(newGroups);
    },
    [],
  );

  const expandAll = useCallback(
    (graph: Graph<NodeAttributes, EdgeAttributes>) => {
      const newGroups = new Map(groupsRef.current);
      for (const [sourceId, state] of newGroups) {
        if (!state.expanded) {
          const newState = { ...state, expanded: true };
          newGroups.set(sourceId, newState);

          const groupNodeId = `${SOURCE_GROUP_PREFIX}${sourceId}`;
          if (graph.hasNode(groupNodeId)) {
            graph.setNodeAttribute(groupNodeId, 'label', state.group.title);
          }
        }
      }
      setGroups(newGroups);
    },
    [],
  );

  const collapseAll = useCallback(
    (graph: Graph<NodeAttributes, EdgeAttributes>) => {
      const newGroups = new Map(groupsRef.current);
      for (const [sourceId, state] of newGroups) {
        if (state.expanded) {
          const newState = { ...state, expanded: false };
          newGroups.set(sourceId, newState);

          const groupNodeId = `${SOURCE_GROUP_PREFIX}${sourceId}`;
          if (graph.hasNode(groupNodeId)) {
            graph.setNodeAttribute(
              groupNodeId,
              'label',
              `${state.group.title} (${state.memberNodeIds.length})`,
            );
          }
        }
      }
      setGroups(newGroups);
    },
    [],
  );

  const getNodeSourceGroup = useCallback((nodeId: string) => {
    for (const state of groupsRef.current.values()) {
      if (state.memberNodeIds.includes(nodeId)) return state;
    }
    return undefined;
  }, []);

  return {
    groups,
    collapsedMemberIds,
    collapsedSourceIds,
    loadSourceGroups,
    toggleGroup,
    expandAll,
    collapseAll,
    getNodeSourceGroup,
    loading,
  };
}
