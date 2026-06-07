// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * PropertiesPanel: Right sidebar for editing selected node/edge properties
 *
 * Orchestrates per-node-type sub-panels (StepNodePanel, TriggerNodePanel,
 * ConditionalNodePanel, EdgePanel) and manages the common header, footer
 * actions, and local state via the usePropertyPanelState hook.
 */

import React from 'react';
import { Box, Typography, IconButton, Button } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import DeleteIcon from '@mui/icons-material/Delete';
import SaveIcon from '@mui/icons-material/Save';
import BookmarkIcon from '@mui/icons-material/Bookmark';
import type { Node, Edge } from '@xyflow/react';
import type {
  WorkflowStepNodeData,
  ConditionalNodeData,
  FieldSchema,
} from '../../types';
import type { FieldSource } from '../../utils/fieldClassification';
import { ghostButtonSx } from '../../../../theme/ghostStyles';
import { ChaosCypherPalette } from '../../../../theme/palette';
import { usePropertyPanelState } from '../../hooks/usePropertyPanelState';
import { EdgePanel } from './EdgePanel';
import { StepNodePanel } from './StepNodePanel';
import { TriggerNodePanel } from './TriggerNodePanel';
import { ConditionalNodePanel } from './ConditionalNodePanel';

/**
 * Get the display label for the delete button based on node type.
 */
function getDeleteButtonLabel(nodeType: string): string {
  switch (nodeType) {
    case 'eventTriggerNode': return 'Trigger';
    case 'triggerNode':
    case 'unifiedEntryNode': return 'Start';
    case 'conditionalNode': return 'Condition';
    default: return 'Step';
  }
}

interface PropertiesPanelProps {
  selectedNode: Node | null;
  selectedEdge: Edge | null;
  onNodeUpdate: (nodeId: string, data: Partial<WorkflowStepNodeData>) => void;
  onDeleteNode: () => void;
  onDeleteEdge: () => void;
  onClose: () => void;
  onSaveAsTemplate?: (name: string, nodeData: WorkflowStepNodeData) => void;
  /** Tool input schema for dynamic form generation */
  toolSchema?: Record<string, unknown> | null;
  /** Tool output schema for displaying outputs */
  toolOutputSchema?: FieldSchema[];
  /** Available fields from upstream nodes */
  upstreamFields?: FieldSource[];
}

export const PropertiesPanel: React.FC<PropertiesPanelProps> = ({
  selectedNode,
  selectedEdge,
  onNodeUpdate,
  onDeleteNode,
  onDeleteEdge,
  onClose,
  onSaveAsTemplate,
  toolSchema,
  toolOutputSchema = [],
  upstreamFields = [],
}) => {
  const state = usePropertyPanelState(selectedNode, onNodeUpdate, onSaveAsTemplate);

  // No selection state
  if (!selectedNode && !selectedEdge) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <Typography variant="body2" sx={{
          color: "text.secondary"
        }}>
          Select a node or edge to view its properties.
        </Typography>
      </Box>
    );
  }

  // Edge selected — delegate to EdgePanel
  if (selectedEdge) {
    return (
      <EdgePanel
        selectedEdge={selectedEdge}
        onDeleteEdge={onDeleteEdge}
        onClose={onClose}
      />
    );
  }

  // Node selected
  const nodeType = selectedNode?.type;

  /** Render the appropriate sub-panel for the selected node type. */
  const renderNodeContent = () => {
    if (nodeType === 'stepNode' || nodeType === 'multiPortStepNode') {
      return (
        <StepNodePanel
          nodeData={state.localData as unknown as WorkflowStepNodeData}
          showJsonEditor={state.showJsonEditor}
          onToggleJsonEditor={state.toggleJsonEditor}
          onChange={state.handleChange}
          toolSchema={toolSchema ?? null}
          toolOutputSchema={toolOutputSchema}
          upstreamFields={upstreamFields}
        />
      );
    }

    if (
      nodeType === 'triggerNode' ||
      nodeType === 'unifiedEntryNode' ||
      nodeType === 'eventTriggerNode'
    ) {
      return (
        <TriggerNodePanel
          nodeType={nodeType}
          nodeData={state.localData}
          localFilterRules={state.localFilterRules}
          onChange={state.handleChange}
          onFilterChange={state.handleFilterChange}
        />
      );
    }

    if (nodeType === 'conditionalNode') {
      return (
        <ConditionalNodePanel
          nodeData={state.localData as unknown as ConditionalNodeData}
          showJsonEditor={state.showJsonEditor}
          onToggleJsonEditor={state.toggleJsonEditor}
          onChange={state.handleChange}
          conditionGroup={state.conditionGroup}
          upstreamFields={upstreamFields}
        />
      );
    }

    return null;
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography variant="subtitle1" sx={{
            fontWeight: 600
          }}>
            {nodeType === 'eventTriggerNode'
              ? 'Trigger Properties'
              : nodeType === 'triggerNode' || nodeType === 'unifiedEntryNode'
              ? 'Start Properties'
              : nodeType === 'conditionalNode'
              ? 'Condition Properties'
              : 'Step Properties'}
          </Typography>
          <IconButton aria-label="Close" size="small" onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>
      </Box>

      {/* Scrollable content — delegates to the appropriate sub-panel */}
      <Box sx={{ flex: 1, overflow: 'auto' }}>
        {renderNodeContent()}
      </Box>

      {/* Footer actions */}
      <Box sx={{ p: 2, borderTop: 1, borderColor: 'divider' }}>
        {state.isDirty && (
          <Button
            variant="outlined"
            sx={{ ...ghostButtonSx(ChaosCypherPalette.primary), mb: 1 }}
            startIcon={<SaveIcon />}
            onClick={state.handleApply}
            fullWidth
          >
            Apply Changes
          </Button>
        )}

        {(nodeType === 'stepNode' || nodeType === 'multiPortStepNode') && (
          <Button
            variant="outlined"
            startIcon={<BookmarkIcon />}
            onClick={state.handleSaveAsTemplate}
            fullWidth
            sx={{ mb: 1 }}
          >
            Save as Template
          </Button>
        )}

        <Button
          variant="outlined"
          sx={ghostButtonSx(ChaosCypherPalette.error)}
          startIcon={<DeleteIcon />}
          onClick={onDeleteNode}
          fullWidth
        >
          Delete {getDeleteButtonLabel(nodeType || '')}
        </Button>
      </Box>
    </Box>
  );
};
