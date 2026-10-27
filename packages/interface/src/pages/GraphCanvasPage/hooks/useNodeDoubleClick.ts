// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useNodeDoubleClick: what a double-click on a canvas node does.
 *
 * - An Unresolved hub: show or hide that hub's members.
 * - A source's root: expand or collapse its source group.
 * - A real node: open it in the properties panel.
 */

import { useCallback } from 'react';
import type { GraphNodeData } from '../types';
import { SOURCE_GROUP_PREFIX, isSourceGroupNode, isUnresolvedBucketNode } from '../types';

interface UseNodeDoubleClickProps {
  onToggleSourceGroup: (sourceId: string) => void;
  onToggleUnresolved: (hubId: string) => void;
  onOpenNode: (data: GraphNodeData) => void;
}

export function useNodeDoubleClick({
  onToggleSourceGroup,
  onToggleUnresolved,
  onOpenNode,
}: UseNodeDoubleClickProps): (nodeId: string, data: GraphNodeData) => void {
  return useCallback(
    (nodeId: string, data: GraphNodeData) => {
      if (isUnresolvedBucketNode(nodeId)) {
        onToggleUnresolved(nodeId);
      } else if (isSourceGroupNode(nodeId)) {
        onToggleSourceGroup(nodeId.slice(SOURCE_GROUP_PREFIX.length));
      } else {
        onOpenNode(data);
      }
    },
    [onToggleSourceGroup, onToggleUnresolved, onOpenNode],
  );
}
