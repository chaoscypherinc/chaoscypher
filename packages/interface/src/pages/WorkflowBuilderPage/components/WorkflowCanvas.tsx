// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowCanvas: ReactFlow canvas with drag-and-drop support.
 *
 * Renders the interactive ReactFlow graph, background, minimap, controls,
 * an empty-state prompt, and a palette-toggle button. All mutation callbacks
 * are injected via props from the parent orchestrator.
 */

import React, { useMemo, type DragEvent } from 'react';
import {
  ReactFlow,
  Controls,
  MiniMap,
  Background,
  BackgroundVariant,
  ConnectionLineType,
  Panel,
  type Node,
  type Edge,
  type Connection,
  type NodeChange,
  type EdgeChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Box, Alert, IconButton, Tooltip, Typography, useTheme } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import { CardColors } from '../../../theme/cardStyles';
import { GraphColors, StatusColors } from '../../../theme/colors';

// Custom node components
import { WorkflowStepNode } from './nodes/WorkflowStepNode';
import { TriggerNode } from './nodes/TriggerNode';
import { EventTriggerNode } from './nodes/EventTriggerNode';
import { ConditionalNode } from './nodes/ConditionalNode';
import { UnifiedEntryNode } from './nodes/UnifiedEntryNode';
import { MultiPortStepNode } from './nodes/MultiPortStepNode';

// Custom edge components
import { WorkflowEdge } from './edges/WorkflowEdge';
import { ConditionalEdge } from './edges/ConditionalEdge';
import { DataFlowEdge } from './edges/DataFlowEdge';

// ---------------------------------------------------------------------------
// Registrations (stable references outside the component)
// ---------------------------------------------------------------------------

const nodeTypes = {
  triggerNode: TriggerNode,
  eventTriggerNode: EventTriggerNode,
  stepNode: WorkflowStepNode,
  conditionalNode: ConditionalNode,
  unifiedEntryNode: UnifiedEntryNode,
  multiPortStepNode: MultiPortStepNode,
};

const edgeTypes = {
  workflowEdge: WorkflowEdge,
  conditionalEdge: ConditionalEdge,
  dataFlowEdge: DataFlowEdge,
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface WorkflowCanvasProps {
  /** Current ReactFlow nodes. */
  nodes: Node[];
  /** Current ReactFlow edges. */
  edges: Edge[];
  /** Handler for node change events. */
  onNodesChange: (changes: NodeChange[]) => void;
  /** Handler for edge change events. */
  onEdgesChange: (changes: EdgeChange[]) => void;
  /** Handler for new connections. */
  onConnect: (connection: Connection) => void;
  /** Handler for node click. */
  onNodeClick: (_event: React.MouseEvent, node: Node) => void;
  /** Handler for edge click. */
  onEdgeClick: (_event: React.MouseEvent, edge: Edge) => void;
  /** Handler for pane click (deselect). */
  onPaneClick: () => void;
  /** Whether a drag is currently over the canvas. */
  isDragOver: boolean;
  /** Handler for drop events. */
  onDrop: (event: DragEvent<HTMLDivElement>) => void;
  /** Handler for drag-over events. */
  onDragOver: (event: DragEvent<HTMLDivElement>) => void;
  /** Handler for drag-leave events. */
  onDragLeave: () => void;
  /** Whether the tool palette is open. */
  isPaletteOpen: boolean;
  /** Open the tool palette. */
  onOpenPalette: () => void;
  /** Current error message to display (null = hidden). */
  error: string | null;
  /** Clear the error message. */
  onClearError: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Interactive ReactFlow canvas for the workflow builder.
 *
 * Displays nodes and edges with snap-to-grid, minimap, controls, and
 * a drag-and-drop overlay indicator. Shows an empty-state panel when
 * no nodes are present.
 */
export const WorkflowCanvas: React.FC<WorkflowCanvasProps> = ({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onNodeClick,
  onEdgeClick,
  onPaneClick,
  isDragOver,
  onDrop,
  onDragOver,
  onDragLeave,
  isPaletteOpen,
  onOpenPalette,
  error,
  onClearError,
}) => {
  const theme = useTheme();

  const reactFlowStyles = useMemo(
    () =>
      ({
        '--controls-bg': theme.palette.background.paper,
        '--controls-border': theme.palette.divider,
        '--controls-button-bg': theme.palette.background.paper,
        '--controls-button-hover': theme.palette.action.hover,
        '--controls-text': theme.palette.text.primary,
        '--minimap-bg': theme.palette.background.paper,
        '--minimap-node': theme.palette.primary.main,
        '--minimap-node-border': theme.palette.primary.dark,
        '--background-color':
          theme.palette.mode === 'dark' ? GraphColors.dark.background : GraphColors.light.background,
        '--background-pattern': theme.palette.divider,
        '--wf-primary': theme.palette.primary.main,
        '--wf-success': theme.palette.success?.main ?? CardColors.success,
        '--wf-error': theme.palette.error?.main ?? StatusColors.failed,
        '--wf-surface': theme.palette.background.paper,
        '--wf-border': theme.palette.divider,
        '--wf-text': theme.palette.text.primary,
      }) as React.CSSProperties,
    [theme],
  );

  return (
    <Box
      className={`workflow-builder-canvas ${isDragOver ? 'drag-over' : ''}`}
      sx={{ flex: 1, position: 'relative' }}
      style={reactFlowStyles}
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
    >
      {/* Error alert */}
      {error && (
        <Alert
          severity="error"
          onClose={onClearError}
          sx={{
            position: 'absolute',
            top: 16,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 1000,
          }}
        >
          {error}
        </Alert>
      )}

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onPaneClick={onPaneClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        minZoom={0.1}
        maxZoom={4}
        defaultEdgeOptions={{ type: 'workflowEdge', animated: false }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={true}
        nodesConnectable={true}
        elementsSelectable={true}
        selectNodesOnDrag={false}
        connectionLineType={ConnectionLineType.Bezier}
        snapToGrid={true}
        snapGrid={[16, 16]}
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
        <MiniMap
          zoomable
          pannable
          nodeColor={(node) => {
            if (node.type === 'triggerNode') return CardColors.success;
            if (node.type === 'conditionalNode') return CardColors.warning;
            return theme.palette.primary.main;
          }}
          maskColor={
            theme.palette.mode === 'dark' ? 'rgba(255, 255, 255, 0.15)' : 'rgba(0, 0, 0, 0.15)'
          }
          style={{
            bottom: 10,
            right: 10,
            width: 200,
            height: 120,
            backgroundColor: theme.palette.background.paper,
          }}
        />
        <Controls />

        {/* Empty state */}
        {nodes.length === 0 && (
          <Panel position="top-center">
            <Box
              sx={{
                textAlign: 'center',
                mt: 10,
                p: 4,
                bgcolor: 'background.paper',
                borderRadius: 2,
                boxShadow: 2,
              }}
            >
              <AddIcon sx={{ fontSize: 48, color: 'text.secondary', mb: 2 }} />
              <Typography variant="h6" gutterBottom>
                Start Building Your Workflow
              </Typography>
              <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                Drag tools from the left panel onto the canvas,
                <br />
                or click a tool to add it.
              </Typography>
            </Box>
          </Panel>
        )}
      </ReactFlow>

      {/* Palette toggle button when closed */}
      {!isPaletteOpen && (
        <Tooltip title="Open Tool Palette">
          <IconButton
            aria-label="Open Tool Palette"
            onClick={onOpenPalette}
            sx={{
              position: 'absolute',
              top: 10,
              left: 10,
              bgcolor: 'background.paper',
              boxShadow: 2,
              '&:hover': { bgcolor: 'background.paper' },
            }}
          >
            <AddIcon />
          </IconButton>
        </Tooltip>
      )}
    </Box>
  );
};
