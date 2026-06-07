// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * PropertiesPanel: Slide-in drawer for graph node/edge properties.
 *
 * Orchestrates sub-components for node editing, edge display, source
 * group properties, and provenance. Uses useNodePropertyState for
 * local editing state and useConfirmDialog for delete confirmations.
 */

import React, { useState } from 'react';
import { Drawer, Box, Typography, IconButton } from '@mui/material';
import CloseIcon from '@mui/icons-material/CloseOutlined';
import type Graph from 'graphology';
import ConfirmDialog from '../../../components/ConfirmDialog';
import { hexToRgba } from '../../../theme/cardStyles';
import { ChaosCypherBackground } from '../../../theme/palette';
import { Overlays } from '../../../theme/overlays';
import type { GraphNodeData, GraphEdgeData, NodeAttributes, EdgeAttributes } from '../types';
import { isSourceGroupNode } from '../types';
import type { SourceGroupState } from '../hooks/useSourceGroups';
import { useNodePropertyState } from '../hooks/useNodePropertyState';
import NodePropertiesForm from './NodePropertiesForm';
import EdgePropertiesView from './EdgePropertiesView';
import SourceGroupProperties from './SourceGroupProperties';

interface PropertiesPanelProps {
  open: boolean;
  onClose: () => void;
  selectedNodeId: string | null;
  selectedNodeData: GraphNodeData | null;
  selectedEdgeId: string | null;
  selectedEdgeData: GraphEdgeData | null;
  onNodeUpdate: (nodeId: string, updates: { label?: string; properties?: Record<string, unknown> }) => void;
  onNodeDelete: (nodeId: string) => void;
  onEdgeDelete: (edgeId: string) => void;
  /** Get source group state for a given node */
  getNodeSourceGroup?: (nodeId: string) => SourceGroupState | undefined;
  /** Toggle expand/collapse for a source group */
  onToggleSourceGroup?: (sourceId: string) => void;
  /** Navigate to a route */
  onNavigate?: (path: string) => void;
  /** Select a node by ID (used for provenance link clicks) */
  onSelectNode?: (nodeId: string) => void;
  /** The graphology graph instance for reading connections. */
  graph?: Graph<NodeAttributes, EdgeAttributes>;
}

const PANEL_WIDTH = 400;

export const PropertiesPanel: React.FC<PropertiesPanelProps> = ({
  open,
  onClose,
  selectedNodeId,
  selectedNodeData,
  selectedEdgeId,
  selectedEdgeData,
  onNodeUpdate,
  onNodeDelete,
  onEdgeDelete,
  getNodeSourceGroup,
  onToggleSourceGroup,
  onNavigate,
  onSelectNode,
  graph,
}) => {
  const nodeState = useNodePropertyState(selectedNodeData);
  const [confirmDeleteNode, setConfirmDeleteNode] = useState(false);
  const [confirmDeleteEdge, setConfirmDeleteEdge] = useState(false);

  const handleSaveNode = () => {
    if (!selectedNodeId) return;
    onNodeUpdate(selectedNodeId, {
      label: nodeState.nodeTitle,
      properties: nodeState.nodeProperties,
    });
    nodeState.clearChanges();
  };

  const handleConfirmDeleteNode = () => {
    if (selectedNodeId) {
      onNodeDelete(selectedNodeId);
      onClose();
    }
    setConfirmDeleteNode(false);
  };

  const handleConfirmDeleteEdge = () => {
    if (selectedEdgeId) {
      onEdgeDelete(selectedEdgeId);
      onClose();
    }
    setConfirmDeleteEdge(false);
  };

  const isSourceGroup = selectedNodeId != null && isSourceGroupNode(selectedNodeId);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={{
        '& .MuiBackdrop-root': {
          backgroundColor: 'transparent',
        },
        '& .MuiDrawer-paper': {
          width: PANEL_WIDTH,
          top: 64,
          height: 'calc(100% - 64px)',
          zIndex: 1300,
          bgcolor: hexToRgba(ChaosCypherBackground.dark.default, 0.85),
          backdropFilter: 'blur(16px)',
          borderLeft: `1px solid ${Overlays.light.dark}`,
        },
      }}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Header */}
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', p: 2, borderBottom: 1, borderColor: 'divider' }}>
          <Typography variant="h6">Properties</Typography>
          <IconButton aria-label="Close" onClick={onClose} size="small">
            <CloseIcon />
          </IconButton>
        </Box>

        {/* Content */}
        <Box sx={{ flex: 1, overflow: 'auto', p: 2 }}>
          {selectedNodeId && isSourceGroup && selectedNodeData && (
            <SourceGroupProperties
              nodeId={selectedNodeId}
              nodeData={selectedNodeData}
              onToggleSourceGroup={onToggleSourceGroup}
              onNavigate={onNavigate}
            />
          )}
          {selectedNodeData && !isSourceGroup && (
            <NodePropertiesForm
              selectedNodeId={selectedNodeId}
              selectedNodeData={selectedNodeData}
              nodeTitle={nodeState.nodeTitle}
              onTitleChange={nodeState.setNodeTitle}
              nodeProperties={nodeState.nodeProperties}
              onPropertyChange={nodeState.handlePropertyChange}
              nodeTags={nodeState.nodeTags}
              newTag={nodeState.newTag}
              onNewTagChange={nodeState.setNewTag}
              onAddTag={nodeState.handleAddTag}
              onDeleteTag={nodeState.handleDeleteTag}
              hasChanges={nodeState.hasChanges}
              onMarkChanged={nodeState.markChanged}
              template={nodeState.template}
              loadingTemplate={nodeState.loadingTemplate}
              onSave={handleSaveNode}
              onDelete={() => setConfirmDeleteNode(true)}
              getNodeSourceGroup={getNodeSourceGroup}
              onSelectNode={onSelectNode}
              graph={graph}
            />
          )}
          {selectedEdgeData && !selectedNodeData && (
            <EdgePropertiesView
              edgeId={selectedEdgeId}
              edgeData={selectedEdgeData}
              onDeleteEdge={() => setConfirmDeleteEdge(true)}
            />
          )}
          {!selectedNodeData && !selectedEdgeData && (
            <Typography
              variant="body2"
              align="center"
              sx={{
                color: "text.secondary",
                mt: 4
              }}>
              Select an item or link to view properties
            </Typography>
          )}
        </Box>
      </Box>
      <ConfirmDialog
        open={confirmDeleteNode}
        title="Confirm Delete"
        message="Are you sure you want to delete this item?"
        onConfirm={handleConfirmDeleteNode}
        onCancel={() => setConfirmDeleteNode(false)}
      />
      <ConfirmDialog
        open={confirmDeleteEdge}
        title="Confirm Delete"
        message="Are you sure you want to delete this link?"
        onConfirm={handleConfirmDeleteEdge}
        onCancel={() => setConfirmDeleteEdge(false)}
      />
    </Drawer>
  );
};
