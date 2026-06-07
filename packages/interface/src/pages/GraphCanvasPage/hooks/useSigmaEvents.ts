// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useSigmaEvents: Centralizes sigma event registration.
 *
 * Maps sigma click/right-click events to selection, context menu,
 * and panel-opening callbacks.
 */

import { useEffect } from 'react';
import { useSigma } from '@react-sigma/core';
import type { SigmaNodeEventPayload, SigmaEdgeEventPayload, SigmaStageEventPayload } from 'sigma/types';
import type { GraphNodeData, GraphEdgeData, NodeAttributes, EdgeAttributes } from '../types';

function extractNodeData(attrs: NodeAttributes): GraphNodeData {
  return {
    nodeId: attrs.nodeId,
    title: attrs.title,
    content: attrs.content,
    templateId: attrs.templateId,
    type: attrs.type,
    tags: attrs.tags,
    createdAt: attrs.createdAt,
    updatedAt: attrs.updatedAt,
    sourceDocumentId: attrs.sourceDocumentId,
    sourceDocumentName: attrs.sourceDocumentName,
  };
}

function extractEdgeData(attrs: EdgeAttributes): GraphEdgeData {
  return {
    edgeId: attrs.edgeId,
    label: attrs.label,
    templateId: attrs.templateId,
    sourceId: attrs.sourceId,
    targetId: attrs.targetId,
    type: attrs.type,
    properties: attrs.properties,
    createdAt: attrs.createdAt,
    updatedAt: attrs.updatedAt,
  };
}

interface UseSigmaEventsProps {
  onNodeClick: (nodeId: string, data: GraphNodeData) => void;
  onEdgeClick: (edgeId: string, data: GraphEdgeData) => void;
  onStageClick: () => void;
  onNodeRightClick: (nodeId: string, data: GraphNodeData, event: MouseEvent) => void;
  onEdgeRightClick: (edgeId: string, data: GraphEdgeData, event: MouseEvent) => void;
  onStageRightClick: (event: MouseEvent) => void;
  onNodeDoubleClick: (nodeId: string, data: GraphNodeData) => void;
}

export function useSigmaEvents({
  onNodeClick,
  onEdgeClick,
  onStageClick,
  onNodeRightClick,
  onEdgeRightClick,
  onStageRightClick,
  onNodeDoubleClick,
}: UseSigmaEventsProps) {
  const sigma = useSigma<NodeAttributes, EdgeAttributes>();

  useEffect(() => {
    const graph = sigma.getGraph();

    const handleClickNode = (payload: SigmaNodeEventPayload) => {
      const attrs = graph.getNodeAttributes(payload.node) as NodeAttributes;
      onNodeClick(payload.node, extractNodeData(attrs));
    };

    const handleClickEdge = (payload: SigmaEdgeEventPayload) => {
      const attrs = graph.getEdgeAttributes(payload.edge) as EdgeAttributes;
      onEdgeClick(payload.edge, extractEdgeData(attrs));
    };

    const handleClickStage = (_payload: SigmaStageEventPayload) => {
      onStageClick();
    };

    const handleRightClickNode = (payload: SigmaNodeEventPayload) => {
      payload.event.original.preventDefault();
      const orig = payload.event.original;
      if (!(orig instanceof MouseEvent)) return;
      const attrs = graph.getNodeAttributes(payload.node) as NodeAttributes;
      onNodeRightClick(payload.node, extractNodeData(attrs), orig);
    };

    const handleRightClickEdge = (payload: SigmaEdgeEventPayload) => {
      payload.event.original.preventDefault();
      const orig = payload.event.original;
      if (!(orig instanceof MouseEvent)) return;
      const attrs = graph.getEdgeAttributes(payload.edge) as EdgeAttributes;
      onEdgeRightClick(payload.edge, extractEdgeData(attrs), orig);
    };

    const handleRightClickStage = (payload: SigmaStageEventPayload) => {
      payload.event.original.preventDefault();
      const orig = payload.event.original;
      if (!(orig instanceof MouseEvent)) return;
      onStageRightClick(orig);
    };

    const handleDoubleClickNode = (payload: SigmaNodeEventPayload) => {
      payload.event.original.preventDefault();
      const attrs = graph.getNodeAttributes(payload.node) as NodeAttributes;
      onNodeDoubleClick(payload.node, extractNodeData(attrs));
    };

    sigma.on('clickNode', handleClickNode);
    sigma.on('clickEdge', handleClickEdge);
    sigma.on('clickStage', handleClickStage);
    sigma.on('rightClickNode', handleRightClickNode);
    sigma.on('rightClickEdge', handleRightClickEdge);
    sigma.on('rightClickStage', handleRightClickStage);
    sigma.on('doubleClickNode', handleDoubleClickNode);

    return () => {
      sigma.off('clickNode', handleClickNode);
      sigma.off('clickEdge', handleClickEdge);
      sigma.off('clickStage', handleClickStage);
      sigma.off('rightClickNode', handleRightClickNode);
      sigma.off('rightClickEdge', handleRightClickEdge);
      sigma.off('rightClickStage', handleRightClickStage);
      sigma.off('doubleClickNode', handleDoubleClickNode);
    };
  }, [sigma, onNodeClick, onEdgeClick, onStageClick, onNodeRightClick, onEdgeRightClick, onStageRightClick, onNodeDoubleClick]);
}
