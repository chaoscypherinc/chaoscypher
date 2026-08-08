// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Workflow Builder Orchestration Hook
 *
 * Thin composition root that wires together the lower-level hooks
 * (persistence, undo/redo, canvas interactions, upstream fields,
 * tool schemas) and layers on keyboard shortcuts.
 */

import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import {
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeChange,
  type EdgeChange,
  type Connection,
} from '@xyflow/react';
import { useToolSchemas } from './useToolSchemas';
import { useUndoRedo } from './useUndoRedo';
import { useCanvasInteractions } from './useCanvasInteractions';
import { useUpstreamFields, type UpstreamField } from './useUpstreamFields';
import { useWorkflowPersistence } from './useWorkflowPersistence';
import type {
  WorkflowStepNodeData,
  WorkflowMetadata,
  StepTemplate,
  FieldSchema,
} from '../types';
import type { DragEvent } from 'react';

// ---------------------------------------------------------------------------
// Return type
// ---------------------------------------------------------------------------

interface UseWorkflowBuilderReturn {
  nodes: Node[];
  edges: Edge[];
  handleNodesChange: (changes: NodeChange[]) => void;
  handleEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (connection: Connection) => void;

  workflow: WorkflowMetadata | null;
  isDirty: boolean;

  canUndo: boolean;
  canRedo: boolean;
  handleUndo: () => void;
  handleRedo: () => void;

  selectedNode: Node | null;
  selectedEdge: Edge | null;
  handleNodeClick: (_event: React.MouseEvent, node: Node) => void;
  handleEdgeClick: (_event: React.MouseEvent, edge: Edge) => void;
  handlePaneClick: () => void;

  isPropertiesPanelOpen: boolean;
  handleNodeUpdate: (nodeId: string, data: Partial<WorkflowStepNodeData>) => void;
  deleteSelectedNode: () => void;
  deleteSelectedEdge: () => void;
  handleSaveAsTemplate: (name: string, nodeData: WorkflowStepNodeData) => void;
  selectedToolSchema: Record<string, unknown> | null;
  selectedToolOutputSchema: FieldSchema[];
  upstreamFields: UpstreamField[];

  isPaletteOpen: boolean;
  setIsPaletteOpen: (open: boolean) => void;

  isDragOver: boolean;
  onDrop: (event: DragEvent<HTMLDivElement>) => void;
  onDragOver: (event: DragEvent<HTMLDivElement>) => void;
  onDragLeave: () => void;

  isTestModalOpen: boolean;
  setIsTestModalOpen: (open: boolean) => void;
  isSettingsModalOpen: boolean;
  setIsSettingsModalOpen: (open: boolean) => void;
  isTemplatesPanelOpen: boolean;
  setIsTemplatesPanelOpen: (open: boolean) => void;

  isLoading: boolean;
  isSaving: boolean;
  error: string | null;
  setError: (error: string | null) => void;
  successMessage: string | null;
  setSuccessMessage: (message: string | null) => void;

  handleBack: () => void;
  handleSave: () => void;
  handleTestExecution: () => void;
  handleAutoLayout: () => void;
  handleSettingsSave: (settings: Partial<WorkflowMetadata>) => Promise<void>;
  handleApplyTemplate: (template: StepTemplate) => void;

  confirmLeaveOpen: boolean;
  handleConfirmLeave: () => void;
  setConfirmLeaveOpen: (open: boolean) => void;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Master state hook for the workflow builder canvas.
 *
 * Composes sub-hooks for persistence, undo/redo, canvas interactions,
 * upstream field computation, and tool schemas, then adds keyboard
 * shortcuts and a template-apply wrapper.
 */
export function useWorkflowBuilder(): UseWorkflowBuilderReturn {
  // -- Canvas state ---------------------------------------------------------
  const [nodes, setNodes, rawOnNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, rawOnEdgesChange] = useEdgesState<Edge>([]);

  // -- UI state -------------------------------------------------------------
  const [isPaletteOpen, setIsPaletteOpen] = useState(true);

  // -- Persistence ----------------------------------------------------------
  const persistence = useWorkflowPersistence(nodes, edges, setNodes, setEdges);

  // -- Undo / redo ----------------------------------------------------------
  const { canUndo, canRedo, pushUndoState, handleUndo, handleRedo } = useUndoRedo(
    nodes,
    edges,
    setNodes,
    setEdges,
    persistence.markDirty,
  );

  // -- Canvas interactions --------------------------------------------------
  const canvas = useCanvasInteractions(
    nodes,
    setNodes,
    setEdges,
    rawOnNodesChange,
    rawOnEdgesChange,
    pushUndoState,
    persistence.markDirty,
  );

  // -- Tool schemas ---------------------------------------------------------
  const { getRawSchema, getOutputSchema } = useToolSchemas();

  // -- Upstream fields ------------------------------------------------------
  const upstreamFields = useUpstreamFields(
    canvas.selectedNode,
    nodes,
    edges,
    getRawSchema,
  );

  // -- Tool schema memos ---------------------------------------------------
  const selectedToolSchema = useMemo(() => {
    const node = canvas.selectedNode;
    if (!node) return null;
    if (node.type !== 'stepNode' && node.type !== 'multiPortStepNode') return null;
    const data = node.data as WorkflowStepNodeData;
    if (!data.toolId) return null;
    return getRawSchema(data.toolId)?.input || null;
  }, [canvas.selectedNode, getRawSchema]);

  const selectedToolOutputSchema = useMemo(() => {
    const node = canvas.selectedNode;
    if (!node) return [];
    if (node.type !== 'stepNode' && node.type !== 'multiPortStepNode') return [];
    const data = node.data as WorkflowStepNodeData;
    if (!data.toolId) return [];
    return getOutputSchema(data.toolId);
  }, [canvas.selectedNode, getOutputSchema]);

  // -- Template apply wrapper (binds pushUndoState) -------------------------
  const handleApplyTemplate = useCallback(
    (template: StepTemplate) => {
      persistence.handleApplyTemplate(template, pushUndoState);
    },
    [persistence, pushUndoState],
  );

  // =========================================================================
  // Keyboard shortcuts
  // =========================================================================

  // Keep the latest handlers/selection in a ref so the (mount-once) keydown
  // listener always reads current values without re-subscribing per render.
  const shortcutsRef = useRef({
    handleUndo,
    handleRedo,
    handleSave: persistence.handleSave,
    selectedNode: canvas.selectedNode,
    selectedEdge: canvas.selectedEdge,
    deleteSelectedNode: canvas.deleteSelectedNode,
    deleteSelectedEdge: canvas.deleteSelectedEdge,
  });
  useEffect(() => {
    shortcutsRef.current = {
      handleUndo,
      handleRedo,
      handleSave: persistence.handleSave,
      selectedNode: canvas.selectedNode,
      selectedEdge: canvas.selectedEdge,
      deleteSelectedNode: canvas.deleteSelectedNode,
      deleteSelectedEdge: canvas.deleteSelectedEdge,
    };
  });

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const shortcuts = shortcutsRef.current;
      if (event.ctrlKey && event.key === 'z' && !event.shiftKey) {
        event.preventDefault();
        shortcuts.handleUndo();
      }
      if (
        (event.ctrlKey && event.key === 'y') ||
        (event.ctrlKey && event.shiftKey && event.key === 'z')
      ) {
        event.preventDefault();
        shortcuts.handleRedo();
      }
      if (event.ctrlKey && event.key === 's') {
        event.preventDefault();
        shortcuts.handleSave();
      }
      if (event.key === 'Delete' || event.key === 'Backspace') {
        const target = event.target as HTMLElement;
        const isInputField =
          target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable;
        if (!isInputField) {
          if (shortcuts.selectedNode) {
            shortcuts.deleteSelectedNode();
          } else if (shortcuts.selectedEdge) {
            shortcuts.deleteSelectedEdge();
          }
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // =========================================================================
  // Return
  // =========================================================================

  return {
    nodes,
    edges,
    handleNodesChange: canvas.handleNodesChange,
    handleEdgesChange: canvas.handleEdgesChange,
    onConnect: canvas.onConnect,

    workflow: persistence.workflow,
    isDirty: persistence.isDirty,

    canUndo,
    canRedo,
    handleUndo,
    handleRedo,

    selectedNode: canvas.selectedNode,
    selectedEdge: canvas.selectedEdge,
    handleNodeClick: canvas.handleNodeClick,
    handleEdgeClick: canvas.handleEdgeClick,
    handlePaneClick: canvas.handlePaneClick,

    isPropertiesPanelOpen: canvas.isPropertiesPanelOpen,
    handleNodeUpdate: canvas.handleNodeUpdate,
    deleteSelectedNode: canvas.deleteSelectedNode,
    deleteSelectedEdge: canvas.deleteSelectedEdge,
    handleSaveAsTemplate: persistence.handleSaveAsTemplate,
    selectedToolSchema,
    selectedToolOutputSchema,
    upstreamFields,

    isPaletteOpen,
    setIsPaletteOpen,

    isDragOver: canvas.isDragOver,
    onDrop: canvas.onDrop,
    onDragOver: canvas.onDragOver,
    onDragLeave: canvas.onDragLeave,

    isTestModalOpen: persistence.isTestModalOpen,
    setIsTestModalOpen: persistence.setIsTestModalOpen,
    isSettingsModalOpen: persistence.isSettingsModalOpen,
    setIsSettingsModalOpen: persistence.setIsSettingsModalOpen,
    isTemplatesPanelOpen: persistence.isTemplatesPanelOpen,
    setIsTemplatesPanelOpen: persistence.setIsTemplatesPanelOpen,

    isLoading: persistence.isLoading,
    isSaving: persistence.isSaving,
    error: persistence.error,
    setError: persistence.setError,
    successMessage: persistence.successMessage,
    setSuccessMessage: persistence.setSuccessMessage,

    handleBack: persistence.handleBack,
    handleSave: persistence.handleSave,
    handleTestExecution: persistence.handleTestExecution,
    handleAutoLayout: persistence.handleAutoLayout,
    handleSettingsSave: persistence.handleSettingsSave,
    handleApplyTemplate,

    confirmLeaveOpen: persistence.confirmLeaveOpen,
    handleConfirmLeave: persistence.handleConfirmLeave,
    setConfirmLeaveOpen: persistence.setConfirmLeaveOpen,
  };
}
