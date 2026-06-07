// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Workflow Serialization Utilities
 *
 * Converts between ReactFlow canvas state and API workflow format.
 * Handles node/edge to step conversion and vice versa.
 */

import type { Node, Edge } from '@xyflow/react';
import type {
  WorkflowStepNodeData,
  TriggerNodeData,
  ConditionalNodeData,
  EventTriggerNodeData,
  WorkflowMetadata,
  ValidationError,
} from '../types';
import type { WorkflowStep, WorkflowStepCreate, WorkflowTrigger } from '../../../services/api/workflows';

// ============================================================================
// Canvas to API Conversion (Serialize)
// ============================================================================

/**
 * Convert canvas nodes and edges to API workflow steps
 */
export function serializeWorkflow(
  nodes: Node[],
  edges: Edge[]
): WorkflowStepCreate[] {
  // Filter to only step nodes (exclude trigger nodes for now)
  const stepNodes = nodes.filter(
    (n) => n.type === 'stepNode' || n.type === 'conditionalNode'
  );

  // Build dependency map from edges
  const dependencyMap = buildDependencyMap(edges);

  // Topological sort for step ordering
  const orderedNodes = topologicalSort(stepNodes, dependencyMap);

  // Convert to API format
  return orderedNodes.map((node, index) => {
    const data = node.data as WorkflowStepNodeData | ConditionalNodeData;

    if (node.type === 'stepNode') {
      const stepData = data as WorkflowStepNodeData;
      return {
        step_number: index + 1,
        name: stepData.name,
        description: stepData.description,
        tool_type: stepData.toolType,
        tool_id: stepData.toolId,
        configuration: stepData.configuration || {},
        depends_on: dependencyMap[node.id] || [],
        continue_on_error: stepData.continueOnError || false,
        thinking_mode: stepData.thinkingMode,
      };
    } else {
      // Conditional node
      const condData = data as ConditionalNodeData;
      return {
        step_number: index + 1,
        name: condData.name || 'Condition',
        description: `Conditional branch: ${JSON.stringify(condData.condition)}`,
        tool_type: 'system_tool' as const,
        tool_id: 'logic.conditional',
        configuration: { condition: condData.condition },
        depends_on: dependencyMap[node.id] || [],
        continue_on_error: false,
      };
    }
  });
}

/**
 * Build dependency map from edges (target -> sources)
 */
function buildDependencyMap(edges: Edge[]): Record<string, string[]> {
  const map: Record<string, string[]> = {};

  for (const edge of edges) {
    if (!map[edge.target]) {
      map[edge.target] = [];
    }
    map[edge.target].push(edge.source);
  }

  return map;
}

/**
 * Topological sort of nodes based on dependencies
 */
function topologicalSort(
  nodes: Node[],
  dependencyMap: Record<string, string[]>
): Node[] {
  const visited = new Set<string>();
  const result: Node[] = [];
  const nodeMap = new Map(nodes.map((n) => [n.id, n]));

  function visit(nodeId: string) {
    if (visited.has(nodeId)) return;
    visited.add(nodeId);

    const deps = dependencyMap[nodeId] || [];
    for (const depId of deps) {
      if (nodeMap.has(depId)) {
        visit(depId);
      }
    }

    const node = nodeMap.get(nodeId);
    if (node) {
      result.push(node);
    }
  }

  for (const node of nodes) {
    visit(node.id);
  }

  return result;
}

// ============================================================================
// API to Canvas Conversion (Deserialize)
// ============================================================================

/**
 * Convert API workflow steps and triggers to canvas nodes and edges
 *
 * @param workflow - Workflow metadata
 * @param steps - Workflow steps from API
 * @param triggers - Optional triggers for this workflow (if provided, creates EventTriggerNodes)
 */
export function deserializeWorkflow(
  _workflow: WorkflowMetadata,
  steps: WorkflowStep[],
  triggers?: WorkflowTrigger[]
): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // Create trigger nodes based on whether we have triggers
  const triggerNodeIds: string[] = [];

  if (triggers && triggers.length > 0) {
    // Create an EventTriggerNode for each trigger
    const TRIGGER_SPACING = 180;
    const startX = 250 - ((triggers.length - 1) * TRIGGER_SPACING) / 2;

    triggers.forEach((trigger, index) => {
      const nodeId = `trigger-${trigger.id}`;
      const triggerNode: Node<EventTriggerNodeData> = {
        id: nodeId,
        type: 'eventTriggerNode',
        position: { x: startX + index * TRIGGER_SPACING, y: 50 },
        data: {
          triggerId: trigger.id,
          name: trigger.name,
          eventSource: trigger.event_source,
          filters: trigger.filters || {},
          workflowInputs: trigger.workflow_inputs,
          enabled: trigger.enabled,
          priority: trigger.priority,
        },
      };
      nodes.push(triggerNode);
      triggerNodeIds.push(nodeId);
    });
  } else {
    // No triggers - create a simple trigger node for manual execution
    const triggerNode: Node<TriggerNodeData> = {
      id: 'trigger',
      type: 'triggerNode',
      position: { x: 250, y: 50 },
      data: {
        eventSource: 'manual',
        filters: {},
        label: 'Manual Trigger',
      },
    };
    nodes.push(triggerNode);
    triggerNodeIds.push('trigger');
  }

  // Convert steps to nodes
  const stepMap = new Map<string, Node>();
  const VERTICAL_SPACING = 150;
  const HORIZONTAL_SPACING = 300;

  steps.forEach((step, index) => {
    const isConditional =
      step.tool_id === 'logic.conditional' || step.tool_type === 'workflow';

    const node: Node = {
      id: step.id,
      type: isConditional ? 'conditionalNode' : 'stepNode',
      position: {
        x: 250 + (index % 3) * HORIZONTAL_SPACING,
        y: 200 + Math.floor(index / 3) * VERTICAL_SPACING,
      },
      data: isConditional
        ? ({
            name: step.name,
            condition: step.configuration?.condition || {
              field: '',
              operator: 'equals',
              value: '',
            },
          } as ConditionalNodeData)
        : ({
            stepId: step.id,
            name: step.name,
            description: step.description,
            toolType: step.tool_type,
            toolId: step.tool_id,
            toolName: step.name,
            toolCategory: getCategoryFromToolId(step.tool_id),
            configuration: step.configuration || {},
            continueOnError: step.continue_on_error,
            thinkingMode: step.thinking_mode,
          } as WorkflowStepNodeData),
    };

    nodes.push(node);
    stepMap.set(step.id, node);
  });

  // Create edges from depends_on
  steps.forEach((step) => {
    if (step.depends_on && step.depends_on.length > 0) {
      step.depends_on.forEach((depId) => {
        edges.push({
          id: `${depId}-${step.id}`,
          source: depId,
          target: step.id,
          type: 'workflowEdge',
        });
      });
    }
  });

  // Connect all trigger nodes to root steps (steps with no dependencies)
  const rootSteps = steps.filter(
    (s) => !s.depends_on || s.depends_on.length === 0
  );

  triggerNodeIds.forEach((triggerNodeId) => {
    rootSteps.forEach((step) => {
      edges.push({
        id: `${triggerNodeId}-${step.id}`,
        source: triggerNodeId,
        target: step.id,
        type: 'workflowEdge',
      });
    });
  });

  // Apply auto-layout if we have many nodes
  if (nodes.length > 1) {
    applyAutoLayout(nodes, edges, triggerNodeIds);
  }

  return { nodes, edges };
}

/**
 * Extract category from tool ID (e.g., "ai.prompt" -> "ai")
 */
function getCategoryFromToolId(toolId: string): string {
  const parts = toolId.split('.');
  return parts[0] || 'other';
}

/**
 * Simple auto-layout algorithm (top-to-bottom flow)
 *
 * @param nodes - All canvas nodes
 * @param edges - All canvas edges
 * @param triggerNodeIds - IDs of trigger nodes (to start layout from)
 */
function applyAutoLayout(
  nodes: Node[],
  edges: Edge[],
  triggerNodeIds: string[] = ['trigger']
): void {
  const VERTICAL_SPACING = 150;
  const HORIZONTAL_OFFSET = 250;
  const TRIGGER_SPACING = 180;

  // Position trigger nodes at the top
  const triggerNodes = nodes.filter((n) => triggerNodeIds.includes(n.id));
  const startX = HORIZONTAL_OFFSET - ((triggerNodes.length - 1) * TRIGGER_SPACING) / 2;
  triggerNodes.forEach((node, idx) => {
    node.position = { x: startX + idx * TRIGGER_SPACING, y: 50 };
  });

  // Build level map based on dependencies
  const levelMap = new Map<string, number>();
  const childrenMap = new Map<string, string[]>();

  // Initialize trigger nodes at level 0
  triggerNodeIds.forEach((id) => levelMap.set(id, 0));

  // Initialize other nodes with undefined level
  nodes.forEach((n) => {
    if (!triggerNodeIds.includes(n.id)) {
      levelMap.set(n.id, -1); // Unvisited
    }
  });

  // Build children map from edges
  edges.forEach((e) => {
    const children = childrenMap.get(e.source) || [];
    if (!children.includes(e.target)) {
      children.push(e.target);
    }
    childrenMap.set(e.source, children);
  });

  // Calculate levels (BFS from all trigger nodes)
  const queue = [...triggerNodeIds];
  while (queue.length > 0) {
    const nodeId = queue.shift()!;
    const currentLevel = levelMap.get(nodeId) || 0;
    const children = childrenMap.get(nodeId) || [];

    children.forEach((childId) => {
      const childLevel = levelMap.get(childId) || -1;
      // Update if unvisited or if we found a longer path
      if (childLevel < currentLevel + 1) {
        levelMap.set(childId, currentLevel + 1);
        queue.push(childId);
      }
    });
  }

  // Group nodes by level (excluding trigger nodes which are already positioned)
  const levelGroups = new Map<number, Node[]>();
  nodes.forEach((n) => {
    if (!triggerNodeIds.includes(n.id)) {
      const level = levelMap.get(n.id) || 1;
      const group = levelGroups.get(level) || [];
      group.push(n);
      levelGroups.set(level, group);
    }
  });

  // Position nodes by level
  levelGroups.forEach((group, level) => {
    const nodeSpacing = 200;
    const groupStartX = HORIZONTAL_OFFSET - ((group.length - 1) * nodeSpacing) / 2;
    group.forEach((node, idx) => {
      node.position = {
        x: groupStartX + idx * nodeSpacing,
        y: 50 + level * VERTICAL_SPACING,
      };
    });
  });
}

// ============================================================================
// Validation
// ============================================================================

/**
 * Validate workflow configuration
 */
export function validateWorkflow(
  nodes: Node[],
  edges: Edge[]
): ValidationError[] {
  const errors: ValidationError[] = [];

  // Check for at least one step
  const stepNodes = nodes.filter(
    (n) => n.type === 'stepNode' || n.type === 'conditionalNode'
  );
  if (stepNodes.length === 0) {
    errors.push({ message: 'Workflow must have at least one step' });
  }

  // Check for disconnected nodes
  const connectedNodes = new Set<string>();
  edges.forEach((e) => {
    connectedNodes.add(e.source);
    connectedNodes.add(e.target);
  });

  stepNodes.forEach((node) => {
    if (!connectedNodes.has(node.id)) {
      errors.push({
        nodeId: node.id,
        message: `Step "${(node.data as WorkflowStepNodeData).name}" is not connected to the workflow`,
      });
    }
  });

  // Check for required configuration in step nodes
  stepNodes.forEach((node) => {
    if (node.type === 'stepNode') {
      const data = node.data as WorkflowStepNodeData;
      if (!data.name) {
        errors.push({
          nodeId: node.id,
          field: 'name',
          message: 'Step name is required',
        });
      }
      if (!data.toolId) {
        errors.push({
          nodeId: node.id,
          field: 'toolId',
          message: 'Tool selection is required',
        });
      }
    }
  });

  // Check for cycles (would cause infinite execution)
  const hasCycle = detectCycle(nodes, edges);
  if (hasCycle) {
    errors.push({ message: 'Workflow contains a cycle, which is not allowed' });
  }

  return errors;
}

/**
 * Detect cycles in the workflow graph
 */
function detectCycle(nodes: Node[], edges: Edge[]): boolean {
  const adjacencyList = new Map<string, string[]>();
  nodes.forEach((n) => adjacencyList.set(n.id, []));
  edges.forEach((e) => {
    const list = adjacencyList.get(e.source) || [];
    list.push(e.target);
    adjacencyList.set(e.source, list);
  });

  const visited = new Set<string>();
  const recursionStack = new Set<string>();

  function hasCycleUtil(nodeId: string): boolean {
    visited.add(nodeId);
    recursionStack.add(nodeId);

    const neighbors = adjacencyList.get(nodeId) || [];
    for (const neighbor of neighbors) {
      if (!visited.has(neighbor)) {
        if (hasCycleUtil(neighbor)) return true;
      } else if (recursionStack.has(neighbor)) {
        return true;
      }
    }

    recursionStack.delete(nodeId);
    return false;
  }

  for (const node of nodes) {
    if (!visited.has(node.id)) {
      if (hasCycleUtil(node.id)) return true;
    }
  }

  return false;
}
