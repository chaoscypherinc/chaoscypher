// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useKeyboardShortcuts: Keyboard shortcuts for the graph canvas.
 *
 * Uses sigma camera for zoom/fit and selection IDs for actions.
 * Returns pending delete state for confirmation dialogs.
 */

import { useEffect, useState, useCallback } from 'react';
import { useSigma } from '@react-sigma/core';
import type { GraphNodeData, NodeAttributes, EdgeAttributes } from '../types';

interface UseKeyboardShortcutsProps {
  selectedNodeId: string | null;
  selectedNodeData: GraphNodeData | null;
  selectedEdgeId: string | null;
  clearSelection: () => void;
  handleNodeDelete: (nodeId: string) => Promise<void>;
  handleEdgeDelete: (edgeId: string) => Promise<void>;
  handleNodeDuplicate: (nodeId: string, data: GraphNodeData) => Promise<void>;
}

interface PendingDelete {
  type: 'node' | 'edge';
  id: string;
  message: string;
}

export function useKeyboardShortcuts({
  selectedNodeId,
  selectedNodeData,
  selectedEdgeId,
  clearSelection,
  handleNodeDelete,
  handleEdgeDelete,
  handleNodeDuplicate,
}: UseKeyboardShortcutsProps) {
  const sigma = useSigma<NodeAttributes, EdgeAttributes>();
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);

  const confirmDelete = useCallback(() => {
    if (!pendingDelete) return;

    if (pendingDelete.type === 'node') {
      handleNodeDelete(pendingDelete.id);
    } else {
      handleEdgeDelete(pendingDelete.id);
    }
    setPendingDelete(null);
  }, [pendingDelete, handleNodeDelete, handleEdgeDelete]);

  const cancelDelete = useCallback(() => {
    setPendingDelete(null);
  }, []);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
        return;
      }

      // Delete selected
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedNodeId && selectedNodeData) {
          e.preventDefault();
          setPendingDelete({
            type: 'node',
            id: selectedNodeId,
            message: `Are you sure you want to delete "${selectedNodeData.title || 'this item'}"?`,
          });
        } else if (selectedEdgeId) {
          e.preventDefault();
          setPendingDelete({
            type: 'edge',
            id: selectedEdgeId,
            message: 'Are you sure you want to delete this link?',
          });
        }
      }

      // Fit view
      if (e.key === 'f' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        sigma.getCamera().animatedReset({ duration: 400 });
      }

      // Zoom in/out
      if (e.key === '=' || e.key === '+') {
        e.preventDefault();
        sigma.getCamera().animatedZoom({ duration: 200 });
      }
      if (e.key === '-' || e.key === '_') {
        e.preventDefault();
        sigma.getCamera().animatedUnzoom({ duration: 200 });
      }

      // Escape to deselect
      if (e.key === 'Escape') {
        clearSelection();
      }

      // Duplicate
      if (e.key === 'd' && !e.ctrlKey && !e.metaKey && selectedNodeId && selectedNodeData) {
        e.preventDefault();
        handleNodeDuplicate(selectedNodeId, selectedNodeData);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [
    sigma,
    selectedNodeId,
    selectedNodeData,
    selectedEdgeId,
    clearSelection,
    handleNodeDelete,
    handleEdgeDelete,
    handleNodeDuplicate,
  ]);

  return { pendingDelete, confirmDelete, cancelDelete };
}
