// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Canvas Interaction Handlers Hook
 *
 * Manages selection, connections, node/edge changes with dirty tracking,
 * node creation from tool drops, drag-and-drop, and deletion for the
 * ReactFlow canvas.
 */

import { useState, useCallback, type DragEvent } from 'react';
import {
  addEdge,
  useReactFlow,
  type Node,
  type Edge,
  type Connection,
  type NodeChange,
  type EdgeChange,
} from '@xyflow/react';
import type {
  WorkflowStepNodeData,
  UnifiedEntryNodeData,
  EventTriggerNodeData,
  SystemTool,
} from '../types';
import { logger } from '../../../utils/logger';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UseCanvasInteractionsReturn {
  // Selection
  selectedNode: Node | null;
  selectedEdge: Edge | null;
  handleNodeClick: (_event: React.MouseEvent, node: Node) => void;
  handleEdgeClick: (_event: React.MouseEvent, edge: Edge) => void;
  handlePaneClick: () => void;

  // Properties panel visibility (driven by selection)
  isPropertiesPanelOpen: boolean;

  // Connection and change handlers
  onConnect: (connection: Connection) => void;
  handleNodesChange: (changes: NodeChange[]) => void;
  handleEdgesChange: (changes: EdgeChange[]) => void;

  // Node update
  handleNodeUpdate: (nodeId: string, data: Partial<WorkflowStepNodeData>) => void;

  // Drag and drop
  isDragOver: boolean;
  onDrop: (event: DragEvent<HTMLDivElement>) => void;
  onDragOver: (event: DragEvent<HTMLDivElement>) => void;
  onDragLeave: () => void;

  // Deletion
  deleteSelectedNode: () => void;
  deleteSelectedEdge: () => void;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Hook encapsulating all ReactFlow canvas interaction logic.
 *
 * @param nodes - Current node array from `useNodesState`
 * @param setNodes - Setter from `useNodesState`
 * @param setEdges - Setter from `useEdgesState`
 * @param onNodesChange - Raw handler from `useNodesState`
 * @param onEdgesChange - Raw handler from `useEdgesState`
 * @param pushUndoState - Captures a snapshot before mutations
 * @param markDirty - Flags unsaved changes
 */
export function useCanvasInteractions(
  nodes: Node[],
  setNodes: React.Dispatch<React.SetStateAction<Node[]>>,
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>,
  onNodesChange: (changes: NodeChange[]) => void,
  onEdgesChange: (changes: EdgeChange[]) => void,
  pushUndoState: () => void,
  markDirty: () => void,
): UseCanvasInteractionsReturn {
  const { screenToFlowPosition } = useReactFlow();

  // -- Selection state ------------------------------------------------------
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null);
  const [isPropertiesPanelOpen, setIsPropertiesPanelOpen] = useState(false);

  // -- Drag state -----------------------------------------------------------
  const [isDragOver, setIsDragOver] = useState(false);

  // =========================================================================
  // Selection
  // =========================================================================

  const handleNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    setSelectedNode(node);
    setSelectedEdge(null);
    setIsPropertiesPanelOpen(true);
  }, []);

  const handleEdgeClick = useCallback((_event: React.MouseEvent, edge: Edge) => {
    setSelectedEdge(edge);
    setSelectedNode(null);
    setIsPropertiesPanelOpen(true);
  }, []);

  const handlePaneClick = useCallback(() => {
    setSelectedNode(null);
    setSelectedEdge(null);
    setIsPropertiesPanelOpen(false);
  }, []);

  // =========================================================================
  // Connection / change handlers
  // =========================================================================

  const onConnect = useCallback(
    (connection: Connection) => {
      pushUndoState();
      setEdges((eds) =>
        addEdge({ ...connection, type: 'workflowEdge', animated: false }, eds),
      );
      markDirty();
    },
    [setEdges, pushUndoState, markDirty],
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const hasMeaningfulChange = changes.some(
        (change) => change.type !== 'select' && change.type !== 'dimensions',
      );
      if (hasMeaningfulChange) {
        markDirty();
      }
      onNodesChange(changes);
    },
    [onNodesChange, markDirty],
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const hasMeaningfulChange = changes.some((change) => change.type !== 'select');
      if (hasMeaningfulChange) {
        markDirty();
      }
      onEdgesChange(changes);
    },
    [onEdgesChange, markDirty],
  );

  // =========================================================================
  // Node creation
  // =========================================================================

  const addStepNode = useCallback(
    (tool: SystemTool, position: { x: number; y: number }) => {
      pushUndoState();
      const newNode: Node<WorkflowStepNodeData> = {
        id: `step-${Date.now()}`,
        type: 'stepNode',
        position,
        data: {
          name: tool.name,
          description: tool.description,
          toolType: 'system_tool',
          toolId: tool.id,
          toolName: tool.name,
          toolCategory: tool.category,
          configuration: {},
          continueOnError: false,
        },
      };
      setNodes((nds) => [...nds, newNode]);
      markDirty();
      setSelectedNode(newNode);
      setIsPropertiesPanelOpen(true);
    },
    [setNodes, pushUndoState, markDirty],
  );

  // =========================================================================
  // Node update
  // =========================================================================

  const handleNodeUpdate = useCallback(
    (nodeId: string, data: Partial<WorkflowStepNodeData>) => {
      pushUndoState();
      setNodes((nds) =>
        nds.map((node) => {
          if (node.id === nodeId) {
            return { ...node, data: { ...node.data, ...data } };
          }
          return node;
        }),
      );
      markDirty();
    },
    [setNodes, pushUndoState, markDirty],
  );

  // =========================================================================
  // Drag and drop
  // =========================================================================

  const onDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragOver(false);

      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });

      // Tool drop
      const toolData = event.dataTransfer.getData('application/workflow-tool');
      if (toolData) {
        try {
          const tool: SystemTool = JSON.parse(toolData);
          addStepNode(tool, position);
        } catch (err) {
          logger.error('Failed to parse dropped tool data:', err);
        }
        return;
      }

      // Workflow-input drop
      const inputData = event.dataTransfer.getData('application/workflow-input');
      if (inputData) {
        const existingEntry = nodes.find((n) => n.type === 'unifiedEntryNode');
        if (existingEntry) {
          setSelectedNode(existingEntry);
          setIsPropertiesPanelOpen(true);
        } else {
          const entryNode: Node<UnifiedEntryNodeData> = {
            id: `entry-${Date.now()}`,
            type: 'unifiedEntryNode',
            position,
            data: {
              label: 'Start',
              workflowInputs: [],
              eventSource: 'manual',
              eventFields: [],
              outputPorts: [],
            },
          };
          pushUndoState();
          setNodes((nds) => [...nds, entryNode]);
          setSelectedNode(entryNode);
          setIsPropertiesPanelOpen(true);
          markDirty();
        }
        return;
      }

      // Event-trigger drop
      const triggerData = event.dataTransfer.getData('application/workflow-trigger');
      if (triggerData) {
        try {
          const trigger = JSON.parse(triggerData);
          const eventTriggerNode: Node<EventTriggerNodeData> = {
            id: `trigger-new-${Date.now()}`,
            type: 'eventTriggerNode',
            position,
            data: {
              triggerId: null,
              name: trigger.name || `${trigger.eventSource} Trigger`,
              eventSource: trigger.eventSource,
              filters: {},
              workflowInputs: null,
              enabled: true,
              priority: 0,
            },
          };
          pushUndoState();
          setNodes((nds) => [...nds, eventTriggerNode]);
          setSelectedNode(eventTriggerNode);
          setIsPropertiesPanelOpen(true);
          markDirty();
        } catch (err) {
          logger.error('Failed to parse dropped trigger data:', err);
        }
      }
    },
    [screenToFlowPosition, addStepNode, nodes, setNodes, pushUndoState, markDirty],
  );

  const onDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    setIsDragOver(true);
  }, []);

  const onDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  // =========================================================================
  // Deletion
  // =========================================================================

  const deleteSelectedNode = useCallback(() => {
    if (!selectedNode) return;
    pushUndoState();
    setNodes((nds) => nds.filter((n) => n.id !== selectedNode.id));
    setEdges((eds) =>
      eds.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id),
    );
    setSelectedNode(null);
    setIsPropertiesPanelOpen(false);
    markDirty();
  }, [selectedNode, setNodes, setEdges, pushUndoState, markDirty]);

  const deleteSelectedEdge = useCallback(() => {
    if (!selectedEdge) return;
    pushUndoState();
    setEdges((eds) => eds.filter((e) => e.id !== selectedEdge.id));
    setSelectedEdge(null);
    setIsPropertiesPanelOpen(false);
    markDirty();
  }, [selectedEdge, setEdges, pushUndoState, markDirty]);

  return {
    selectedNode,
    selectedEdge,
    handleNodeClick,
    handleEdgeClick,
    handlePaneClick,
    isPropertiesPanelOpen,
    onConnect,
    handleNodesChange,
    handleEdgesChange,
    handleNodeUpdate,
    isDragOver,
    onDrop,
    onDragOver,
    onDragLeave,
    deleteSelectedNode,
    deleteSelectedEdge,
  };
}
