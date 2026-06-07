// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowPropertiesPanel: Right-sidebar drawer for editing selected nodes/edges.
 *
 * Wraps the inner PropertiesPanel component inside a persistent MUI Drawer
 * and passes through all required editing callbacks and schema data.
 */

import React from 'react';
import { Drawer } from '@mui/material';
import type { Node, Edge } from '@xyflow/react';

import { PropertiesPanel } from './components/panels/PropertiesPanel';
import type { WorkflowStepNodeData, FieldSchema } from './types';

/** Width of the properties drawer in pixels. */
const PROPERTIES_WIDTH = 360;

interface WorkflowPropertiesPanelProps {
  /** Whether the properties drawer is open. */
  isOpen: boolean;
  /** Currently selected node (null if none). */
  selectedNode: Node | null;
  /** Currently selected edge (null if none). */
  selectedEdge: Edge | null;
  /** Callback to update a node's data by ID. */
  onNodeUpdate: (nodeId: string, data: Partial<WorkflowStepNodeData>) => void;
  /** Callback to delete the selected node. */
  onDeleteNode: () => void;
  /** Callback to delete the selected edge. */
  onDeleteEdge: () => void;
  /** Callback to close the panel. */
  onClose: () => void;
  /** Callback to save a node configuration as a reusable template. */
  onSaveAsTemplate: (name: string, nodeData: WorkflowStepNodeData) => void;
  /** JSON Schema for the selected tool's input. */
  toolSchema: Record<string, unknown> | null;
  /** Output schema fields for the selected tool. */
  toolOutputSchema: FieldSchema[];
  /** Fields available from upstream nodes for variable picking. */
  upstreamFields: {
    nodeId: string;
    nodeName: string;
    field: FieldSchema;
    reference: string;
  }[];
}

/**
 * Persistent right-side drawer containing the node/edge properties editor.
 *
 * The drawer slides in when a node or edge is selected and provides
 * full editing capabilities including schema-driven forms, delete actions,
 * and template saving.
 */
export const WorkflowPropertiesPanel: React.FC<WorkflowPropertiesPanelProps> = ({
  isOpen,
  selectedNode,
  selectedEdge,
  onNodeUpdate,
  onDeleteNode,
  onDeleteEdge,
  onClose,
  onSaveAsTemplate,
  toolSchema,
  toolOutputSchema,
  upstreamFields,
}) => {
  return (
    <Drawer
      variant="persistent"
      anchor="right"
      open={isOpen}
      sx={{
        width: isOpen ? PROPERTIES_WIDTH : 0,
        flexShrink: 0,
        '& .MuiDrawer-paper': {
          width: PROPERTIES_WIDTH,
          position: 'relative',
          borderLeft: 1,
          borderColor: 'divider',
          height: '100%',
          overflow: 'hidden',
        },
      }}
    >
      <PropertiesPanel
        selectedNode={selectedNode}
        selectedEdge={selectedEdge}
        onNodeUpdate={onNodeUpdate}
        onDeleteNode={onDeleteNode}
        onDeleteEdge={onDeleteEdge}
        onClose={onClose}
        onSaveAsTemplate={onSaveAsTemplate}
        toolSchema={toolSchema}
        toolOutputSchema={toolOutputSchema}
        upstreamFields={upstreamFields}
      />
    </Drawer>
  );
};
