// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useGraphSelection: Tracks selected node/edge by ID.
 *
 * Stores IDs rather than full objects — reads data from graphology
 * graph when needed.
 */

import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router';
import type { GraphNodeData, GraphEdgeData } from '../types';

export function useGraphSelection() {
  const navigate = useNavigate();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedNodeData, setSelectedNodeData] = useState<GraphNodeData | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [selectedEdgeData, setSelectedEdgeData] = useState<GraphEdgeData | null>(null);
  const [isPropertiesPanelOpen, setIsPropertiesPanelOpen] = useState(false);
  const [edgeCreationModal, setEdgeCreationModal] = useState<{
    open: boolean;
    sourceId?: string;
    targetId?: string;
  }>({ open: false });

  const handleNodeClick = useCallback((nodeId: string, data: GraphNodeData) => {
    setSelectedNodeId(nodeId);
    setSelectedNodeData(data);
    setSelectedEdgeId(null);
    setSelectedEdgeData(null);
  }, []);

  const handleEdgeClick = useCallback((edgeId: string, data: GraphEdgeData) => {
    setSelectedEdgeId(edgeId);
    setSelectedEdgeData(data);
    setSelectedNodeId(null);
    setSelectedNodeData(null);
    setIsPropertiesPanelOpen(true);
  }, []);

  const handleStageClick = useCallback(() => {
    setSelectedNodeId(null);
    setSelectedNodeData(null);
    setSelectedEdgeId(null);
    setSelectedEdgeData(null);
  }, []);

  const handleCopyNodeId = useCallback((nodeId: string) => {
    navigator.clipboard.writeText(nodeId);
  }, []);

  const handleViewSourceDocument = useCallback(
    (data: GraphNodeData) => {
      const sourceDocumentId = data.content?.source_document_id;
      if (sourceDocumentId) {
        navigate(`/import/${sourceDocumentId}`);
      }
    },
    [navigate],
  );

  const clearSelection = useCallback(() => {
    setSelectedNodeId(null);
    setSelectedNodeData(null);
    setSelectedEdgeId(null);
    setSelectedEdgeData(null);
    setIsPropertiesPanelOpen(false);
  }, []);

  return {
    selectedNodeId,
    selectedNodeData,
    selectedEdgeId,
    selectedEdgeData,
    isPropertiesPanelOpen,
    edgeCreationModal,
    setSelectedNodeId,
    setSelectedNodeData,
    setSelectedEdgeId,
    setSelectedEdgeData,
    setIsPropertiesPanelOpen,
    setEdgeCreationModal,
    handleNodeClick,
    handleEdgeClick,
    handleStageClick,
    handleCopyNodeId,
    handleViewSourceDocument,
    clearSelection,
  };
}
