// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Upstream Fields Hook
 *
 * Computes the output fields available from all upstream nodes for a
 * given selected node.  Used by the properties panel's variable picker
 * to offer auto-complete references such as `{{ steps.<id>.<field> }}`.
 */

import { useMemo } from 'react';
import type { Node, Edge } from '@xyflow/react';
import { parseJsonSchema } from '../utils/schemaParser';
import type {
  WorkflowStepNodeData,
  UnifiedEntryNodeData,
  EventTriggerNodeData,
  FieldSchema,
} from '../types';

/** A single upstream field available for variable references. */
export interface UpstreamField {
  /** ID of the node that produces this field. */
  nodeId: string;
  /** Human-readable name of the producing node. */
  nodeName: string;
  /** Schema of the field. */
  field: FieldSchema;
  /** Template reference string, e.g. `{{ steps.step-1.name }}`. */
  reference: string;
}

/**
 * Computes available upstream output fields for the selected node.
 *
 * Traverses the edge graph backwards from the selected node, collecting
 * output schemas from all upstream step nodes and entry/trigger nodes.
 * Entry and trigger nodes are always included regardless of connectivity.
 *
 * @param selectedNode - Currently selected node (null when nothing selected)
 * @param nodes - All nodes on the canvas
 * @param edges - All edges on the canvas
 * @param getRawSchema - Accessor from `useToolSchemas` to fetch a tool's raw schema
 */
export function useUpstreamFields(
  selectedNode: Node | null,
  nodes: Node[],
  edges: Edge[],
  getRawSchema: (toolId: string) => { input: Record<string, unknown>; output: Record<string, unknown> } | null,
): UpstreamField[] {
  return useMemo(() => {
    if (!selectedNode) return [];
    if (selectedNode.type !== 'stepNode' && selectedNode.type !== 'multiPortStepNode') return [];

    const fields: UpstreamField[] = [];

    // Traverse edges backwards to find upstream nodes
    const upstreamNodeIds = new Set<string>();
    const findUpstream = (nodeId: string) => {
      for (const edge of edges) {
        if (edge.target === nodeId && !upstreamNodeIds.has(edge.source)) {
          upstreamNodeIds.add(edge.source);
          findUpstream(edge.source);
        }
      }
    };
    findUpstream(selectedNode.id);

    // Include entry/trigger nodes even if not directly connected
    for (const node of nodes) {
      if (
        node.type === 'unifiedEntryNode' ||
        node.type === 'triggerNode' ||
        node.type === 'eventTriggerNode'
      ) {
        upstreamNodeIds.add(node.id);
      }
    }

    // Collect output fields from all upstream nodes
    for (const nodeId of upstreamNodeIds) {
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) continue;

      const nodeName =
        (node.data as { name?: string; label?: string }).name ||
        (node.data as { name?: string; label?: string }).label ||
        nodeId;

      if (node.type === 'unifiedEntryNode' || node.type === 'triggerNode') {
        const entryData = node.data as UnifiedEntryNodeData;
        const workflowInputs = entryData.workflowInputs || [];
        const eventFields = entryData.eventFields || [];
        for (const field of [...workflowInputs, ...eventFields]) {
          fields.push({
            nodeId,
            nodeName,
            field,
            reference: `{{ steps.${nodeId}.${field.name} }}`,
          });
        }
      } else if (node.type === 'eventTriggerNode') {
        const triggerData = node.data as EventTriggerNodeData;
        fields.push({
          nodeId,
          nodeName: triggerData.name || nodeName,
          field: {
            name: 'event',
            type: 'object' as const,
            required: true,
            description: 'Event payload data',
          },
          reference: `{{ steps.${nodeId}.event }}`,
        });
      } else if (node.type === 'stepNode' || node.type === 'multiPortStepNode') {
        const stepData = node.data as WorkflowStepNodeData;
        if (stepData.toolId) {
          const rawSchema = getRawSchema(stepData.toolId);
          if (rawSchema?.output) {
            const outputFields = parseJsonSchema(rawSchema.output);
            for (const field of outputFields) {
              fields.push({
                nodeId,
                nodeName,
                field,
                reference: `{{ steps.${nodeId}.${field.name} }}`,
              });
            }
          } else {
            fields.push({
              nodeId,
              nodeName,
              field: {
                name: 'result',
                type: 'any' as const,
                required: true,
                description: 'Step output result',
              },
              reference: `{{ steps.${nodeId}.result }}`,
            });
          }
        }
      }
    }

    return fields;
  }, [selectedNode, nodes, edges, getRawSchema]);
}
