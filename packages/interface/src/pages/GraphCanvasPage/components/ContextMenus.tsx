// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ContextMenus: Context menus for graph nodes, edges, and canvas.
 * Uses MUI Menu for consistent styling with the rest of the UI.
 */

import React from 'react';
import { Menu, MenuItem, Divider } from '@mui/material';
import type { GraphNodeData, GraphEdgeData } from '../types';
import { isSourceGroupNode, SOURCE_GROUP_PREFIX } from '../types';
import type {
  NodeContextMenuController,
  EdgeContextMenuController,
  CanvasContextMenuController,
} from './contextMenuHooks';

// ============= NODE CONTEXT MENU =============

interface NodeContextMenuProps {
  onEdit: (nodeId: string, data: GraphNodeData) => void;
  onDelete: (nodeId: string) => void;
  onDuplicate: (nodeId: string, data: GraphNodeData) => void;
  onCopyId: (nodeId: string) => void;
  onViewSourceDocument?: (nodeId: string, data: GraphNodeData) => void;
  onToggleSourceGroup?: (sourceId: string) => void;
  onNavigateToSource?: (sourceId: string) => void;
  isSourceGroupExpanded?: (sourceId: string) => boolean;
  menuState: NodeContextMenuController;
}

export const NodeContextMenu: React.FC<NodeContextMenuProps> = ({
  onEdit,
  onDelete,
  onDuplicate,
  onCopyId,
  onViewSourceDocument,
  onToggleSourceGroup,
  onNavigateToSource,
  isSourceGroupExpanded,
  menuState,
}) => {
  const { state, close } = menuState;
  const open = state.position !== null;
  const { nodeId, data } = state.props ?? { nodeId: '', data: {} as GraphNodeData };

  // Source group nodes get a different context menu
  if (isSourceGroupNode(nodeId)) {
    const sourceId = nodeId.slice(SOURCE_GROUP_PREFIX.length);
    const expanded = isSourceGroupExpanded?.(sourceId) ?? false;

    return (
      <Menu
        open={open}
        onClose={close}
        anchorReference="anchorPosition"
        anchorPosition={state.position ? { top: state.position.mouseY, left: state.position.mouseX } : undefined}
      >
        {onToggleSourceGroup && (
          <MenuItem onClick={() => { onToggleSourceGroup(sourceId); close(); }}>
            {expanded ? 'Collapse Source Group' : 'Expand Source Group'}
          </MenuItem>
        )}
        {onNavigateToSource && (
          <MenuItem onClick={() => { onNavigateToSource(sourceId); close(); }}>
            View Source Document
          </MenuItem>
        )}
      </Menu>
    );
  }

  return (
    <Menu
      open={open}
      onClose={close}
      anchorReference="anchorPosition"
      anchorPosition={state.position ? { top: state.position.mouseY, left: state.position.mouseX } : undefined}
    >
      <MenuItem onClick={() => { onEdit(nodeId, data); close(); }}>
        Edit Properties
      </MenuItem>
      <MenuItem onClick={() => { onDuplicate(nodeId, data); close(); }}>
        Duplicate Item
      </MenuItem>
      <MenuItem onClick={() => { onCopyId(nodeId); close(); }}>
        Copy Item ID
      </MenuItem>
      {onViewSourceDocument && (
        <MenuItem
          disabled={!data?.content?.source_document_id}
          onClick={() => { onViewSourceDocument(nodeId, data); close(); }}
        >
          View Source Document
        </MenuItem>
      )}
      <Divider />
      <MenuItem onClick={() => { onDelete(nodeId); close(); }}>
        Delete Item
      </MenuItem>
    </Menu>
  );
};

// ============= EDGE CONTEXT MENU =============

interface EdgeContextMenuProps {
  onEdit: (edgeId: string, data: GraphEdgeData) => void;
  onDelete: (edgeId: string) => void;
  onCopyId: (edgeId: string) => void;
  menuState: EdgeContextMenuController;
}

export const EdgeContextMenu: React.FC<EdgeContextMenuProps> = ({
  onEdit,
  onDelete,
  onCopyId,
  menuState,
}) => {
  const { state, close } = menuState;
  const open = state.position !== null;
  const { edgeId, data } = state.props ?? { edgeId: '', data: {} as GraphEdgeData };

  return (
    <Menu
      open={open}
      onClose={close}
      anchorReference="anchorPosition"
      anchorPosition={state.position ? { top: state.position.mouseY, left: state.position.mouseX } : undefined}
    >
      <MenuItem onClick={() => { onEdit(edgeId, data); close(); }}>
        Edit Properties
      </MenuItem>
      <MenuItem onClick={() => { onCopyId(edgeId); close(); }}>
        Copy Link ID
      </MenuItem>
      <Divider />
      <MenuItem onClick={() => { onDelete(edgeId); close(); }}>
        Delete Link
      </MenuItem>
    </Menu>
  );
};

// ============= CANVAS CONTEXT MENU =============

interface CanvasContextMenuProps {
  onCreate: (position: { x: number; y: number }) => void;
  onFitView: () => void;
  onResetLayout: () => void;
  onExpandAllGroups?: () => void;
  onCollapseAllGroups?: () => void;
  hasSourceGroups?: boolean;
  menuState: CanvasContextMenuController;
}

export const CanvasContextMenu: React.FC<CanvasContextMenuProps> = ({
  onCreate,
  onFitView,
  onResetLayout,
  onExpandAllGroups,
  onCollapseAllGroups,
  hasSourceGroups,
  menuState,
}) => {
  const { state, close } = menuState;
  const open = state.position !== null;
  const position = state.props ?? { x: 0, y: 0 };

  return (
    <Menu
      open={open}
      onClose={close}
      anchorReference="anchorPosition"
      anchorPosition={state.position ? { top: state.position.mouseY, left: state.position.mouseX } : undefined}
    >
      <MenuItem onClick={() => { onCreate(position); close(); }}>
        Create Item Here
      </MenuItem>
      <MenuItem onClick={() => { onFitView(); close(); }}>
        Fit View
      </MenuItem>
      <MenuItem onClick={() => { onResetLayout(); close(); }}>
        Reset Layout
      </MenuItem>
      {hasSourceGroups && onExpandAllGroups && onCollapseAllGroups && (
        <>
          <Divider />
          <MenuItem onClick={() => { onExpandAllGroups(); close(); }}>
            Expand All Source Groups
          </MenuItem>
          <MenuItem onClick={() => { onCollapseAllGroups(); close(); }}>
            Collapse All Source Groups
          </MenuItem>
        </>
      )}
    </Menu>
  );
};

