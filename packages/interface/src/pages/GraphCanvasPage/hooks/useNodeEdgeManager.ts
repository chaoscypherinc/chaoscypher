// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useNodeEdgeManager: CRUD operations for nodes and edges.
 *
 * Mutates the graphology graph directly (which triggers sigma re-renders).
 */

import { useCallback } from 'react';
import type Graph from 'graphology';
import { nodeApi } from '../../../services/api/nodes';
import { edgeApi } from '../../../services/api/edges';
import { templateApi } from '../../../services/api/templates';
import { addApiNodeToGraph, addApiEdgeToGraph } from '../utils/transformers';
import type { NodeAttributes, EdgeAttributes, GraphNodeData } from '../types';
import { getColorForTemplate } from '../../../utils/colorUtils';
import { logger } from '../../../utils/logger';
import { getApiErrorMessage } from '../../../utils/errors';

interface NodeUpdatePayload {
  label?: string;
  title?: string;
  content?: Record<string, unknown>;
  properties?: Record<string, unknown>;
  position?: { x: number; y: number };
  template_id?: string;
  type?: string;
  tags?: string[];
}

interface UseNodeEdgeManagerProps {
  graph: Graph<NodeAttributes, EdgeAttributes>;
  setError: (error: string | null) => void;
  setIsPropertiesPanelOpen: (open: boolean) => void;
}

export function useNodeEdgeManager({
  graph,
  setError,
  setIsPropertiesPanelOpen,
}: UseNodeEdgeManagerProps) {
  const handleNodeCreate = useCallback(
    async (templateId: string, position?: { x: number; y: number }) => {
      try {
        const template = await templateApi.get(templateId);

        const properties: Record<string, unknown> = {};
        template.properties.forEach((prop) => {
          if (prop.required) {
            if (prop.default_value !== undefined && prop.default_value !== null) {
              properties[prop.name] = prop.default_value;
            } else {
              switch (prop.property_type) {
                case 'text':
                case 'string':
                  properties[prop.name] = '';
                  break;
                case 'integer':
                case 'float':
                  properties[prop.name] = 0;
                  break;
                case 'boolean':
                  properties[prop.name] = false;
                  break;
                case 'json':
                  properties[prop.name] = {};
                  break;
                case 'node_reference_list':
                  properties[prop.name] = [];
                  break;
                default:
                  properties[prop.name] = '';
              }
            }
          }
        });

        const newNode = await nodeApi.create({
          template_id: templateId,
          label: 'New Node',
          properties,
          position: position ? { x: position.x, y: position.y } : undefined,
        });

        addApiNodeToGraph(graph, newNode, position);
        return newNode.id;
      } catch (err) {
        logger.error('Error creating node:', err);
        setError(getApiErrorMessage(err) || 'Failed to create node');
        throw err;
      }
    },
    [graph, setError],
  );

  const handleNodeUpdate = useCallback(
    async (nodeId: string, updates: NodeUpdatePayload) => {
      try {
        // Preserve position
        let currentPosition: { x: number; y: number } | undefined;
        if (graph.hasNode(nodeId)) {
          const attrs = graph.getNodeAttributes(nodeId);
          currentPosition = { x: attrs.x, y: attrs.y };
        }

        const updateWithPosition = {
          ...updates,
          position: updates.position || currentPosition,
        };

        const updatedNode = await nodeApi.update(nodeId, updateWithPosition);

        // Update graph node attributes
        if (graph.hasNode(nodeId)) {
          const title = updatedNode.title || updatedNode.label || 'Untitled';
          const content = (updatedNode.content || updatedNode.properties || {}) as Record<string, unknown>;
          const templateId = updatedNode.template_id || 'default';

          graph.updateNodeAttributes(nodeId, (prev) => ({
            ...prev,
            title,
            content,
            templateId,
            type: updatedNode.type,
            tags: updatedNode.tags || [],
            updatedAt: updatedNode.updated_at,
            sourceDocumentId:
              typeof content.source_document_id === 'string' ? content.source_document_id : undefined,
            sourceDocumentName:
              typeof content.source_document_name === 'string'
                ? content.source_document_name
                : undefined,
            color: getColorForTemplate(templateId),
            label: title,
            // Preserve position if API didn't return one
            x: currentPosition?.x ?? prev.x,
            y: currentPosition?.y ?? prev.y,
          }));
        }
      } catch (err) {
        logger.error('Error updating node:', err);
        setError(getApiErrorMessage(err) || 'Failed to update node');
      }
    },
    [graph, setError],
  );

  const handleNodeDelete = useCallback(
    async (nodeId: string) => {
      try {
        await nodeApi.delete(nodeId);
        if (graph.hasNode(nodeId)) {
          graph.dropNode(nodeId); // Also removes connected edges
        }
        setIsPropertiesPanelOpen(false);
      } catch (err) {
        logger.error('Error deleting node:', err);
        setError(getApiErrorMessage(err) || 'Failed to delete item');
      }
    },
    [graph, setIsPropertiesPanelOpen, setError],
  );

  const handleNodeDuplicate = useCallback(
    async (nodeId: string, nodeData: GraphNodeData) => {
      try {
        await templateApi.get(nodeData.templateId);
        const properties = { ...nodeData.content };

        let position: { x: number; y: number } | undefined;
        if (graph.hasNode(nodeId)) {
          const attrs = graph.getNodeAttributes(nodeId);
          position = { x: attrs.x + 50, y: attrs.y + 50 };
        }

        const newNode = await nodeApi.create({
          template_id: nodeData.templateId,
          label: `${nodeData.title} (Copy)`,
          properties,
          position,
        });

        addApiNodeToGraph(graph, newNode, position);
      } catch (err) {
        logger.error('Error duplicating node:', err);
        setError(getApiErrorMessage(err) || 'Failed to duplicate item');
      }
    },
    [graph, setError],
  );

  const handleEdgeCreate = useCallback(
    async (sourceId: string, targetId: string, edgeTemplateId: string, label?: string) => {
      try {
        const newEdge = await edgeApi.create({
          source_node_id: sourceId,
          target_node_id: targetId,
          template_id: edgeTemplateId,
          label: label || '',
          properties: {},
        });

        addApiEdgeToGraph(graph, newEdge);
        return newEdge.id;
      } catch (err) {
        logger.error('Error creating edge:', err);
        setError(getApiErrorMessage(err) || 'Failed to create edge');
        throw err;
      }
    },
    [graph, setError],
  );

  const handleEdgeDelete = useCallback(
    async (edgeId: string) => {
      try {
        await edgeApi.delete(edgeId);
        if (graph.hasEdge(edgeId)) {
          graph.dropEdge(edgeId);
        }
        setIsPropertiesPanelOpen(false);
      } catch (err) {
        logger.error('Error deleting edge:', err);
        setError(getApiErrorMessage(err) || 'Failed to delete link');
      }
    },
    [graph, setIsPropertiesPanelOpen, setError],
  );

  return {
    handleNodeCreate,
    handleNodeUpdate,
    handleNodeDelete,
    handleNodeDuplicate,
    handleEdgeCreate,
    handleEdgeDelete,
  };
}
