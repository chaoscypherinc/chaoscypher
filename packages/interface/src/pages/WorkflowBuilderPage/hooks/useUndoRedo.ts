// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Undo/Redo Stack Hook for the Workflow Builder
 *
 * Manages a bounded undo/redo history of canvas snapshots (nodes + edges).
 * Each meaningful mutation should call `pushUndoState` before applying changes
 * so the previous state can be restored.
 */

import { useState, useCallback } from 'react';
import type { Node, Edge } from '@xyflow/react';
import type { CanvasSnapshot } from '../types';

/** Maximum number of undo states retained. */
const UNDO_STACK_MAX = 50;

interface UseUndoRedoReturn {
  /** Whether an undo operation is available. */
  canUndo: boolean;
  /** Whether a redo operation is available. */
  canRedo: boolean;
  /**
   * Snapshot the current canvas state onto the undo stack.
   * Must be called **before** the mutation that should be undoable.
   */
  pushUndoState: () => void;
  /** Restore the previous canvas state. */
  handleUndo: () => void;
  /** Re-apply the most recently undone canvas state. */
  handleRedo: () => void;
}

/**
 * Hook providing undo/redo history for the ReactFlow canvas.
 *
 * @param nodes - Current node array from `useNodesState`
 * @param edges - Current edge array from `useEdgesState`
 * @param setNodes - Setter from `useNodesState`
 * @param setEdges - Setter from `useEdgesState`
 * @param markDirty - Callback invoked after undo/redo to flag unsaved changes
 */
export function useUndoRedo(
  nodes: Node[],
  edges: Edge[],
  setNodes: (nodes: Node[]) => void,
  setEdges: (edges: Edge[]) => void,
  markDirty: () => void,
): UseUndoRedoReturn {
  const [undoStack, setUndoStack] = useState<CanvasSnapshot[]>([]);
  const [redoStack, setRedoStack] = useState<CanvasSnapshot[]>([]);

  const canUndo = undoStack.length > 0;
  const canRedo = redoStack.length > 0;

  const pushUndoState = useCallback(() => {
    const snapshot: CanvasSnapshot = {
      nodes: structuredClone(nodes),
      edges: structuredClone(edges),
      timestamp: Date.now(),
    };
    setUndoStack((prev) => [...prev.slice(-(UNDO_STACK_MAX - 1)), snapshot]);
    setRedoStack([]);
  }, [nodes, edges]);

  const handleUndo = useCallback(() => {
    if (undoStack.length === 0) return;

    const currentState: CanvasSnapshot = {
      nodes: structuredClone(nodes),
      edges: structuredClone(edges),
      timestamp: Date.now(),
    };

    const previousState = undoStack[undoStack.length - 1];
    setUndoStack((prev) => prev.slice(0, -1));
    setRedoStack((prev) => [...prev, currentState]);

    setNodes(previousState.nodes);
    setEdges(previousState.edges);
    markDirty();
  }, [undoStack, nodes, edges, setNodes, setEdges, markDirty]);

  const handleRedo = useCallback(() => {
    if (redoStack.length === 0) return;

    const currentState: CanvasSnapshot = {
      nodes: structuredClone(nodes),
      edges: structuredClone(edges),
      timestamp: Date.now(),
    };

    const nextState = redoStack[redoStack.length - 1];
    setRedoStack((prev) => prev.slice(0, -1));
    setUndoStack((prev) => [...prev, currentState]);

    setNodes(nextState.nodes);
    setEdges(nextState.edges);
    markDirty();
  }, [redoStack, nodes, edges, setNodes, setEdges, markDirty]);

  return { canUndo, canRedo, pushUndoState, handleUndo, handleRedo };
}
