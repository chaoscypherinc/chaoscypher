// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import { serializeWorkflow, deserializeWorkflow, validateWorkflow } from '../serialization';
import type {
  WorkflowStepNodeData,
  ConditionalNodeData,
  TriggerNodeData,
  EventTriggerNodeData,
  WorkflowMetadata,
} from '../../types';
import type { WorkflowStep, WorkflowTrigger } from '../../../../services/api/workflows';

// ============================================================================
// Fixtures
// ============================================================================

function makeWorkflowMetadata(overrides?: Partial<WorkflowMetadata>): WorkflowMetadata {
  return {
    id: 'wf-1',
    name: 'Test Workflow',
    description: 'A test workflow',
    is_system: false,
    is_active: true,
    expose_as_ai_tool: false,
    input_schema: {},
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeStepNode(
  id: string,
  data: Partial<WorkflowStepNodeData> = {}
): Node<WorkflowStepNodeData> {
  return {
    id,
    type: 'stepNode',
    position: { x: 0, y: 0 },
    data: {
      name: `Step ${id}`,
      toolType: 'system_tool',
      toolId: 'ai.prompt',
      toolName: `Step ${id}`,
      toolCategory: 'ai',
      configuration: {},
      continueOnError: false,
      ...data,
    },
  };
}

function makeConditionalNode(
  id: string,
  data: Partial<ConditionalNodeData> = {}
): Node<ConditionalNodeData> {
  return {
    id,
    type: 'conditionalNode',
    position: { x: 0, y: 0 },
    data: {
      name: 'My Condition',
      condition: {
        field: 'status',
        operator: 'equals',
        value: 'ok',
      },
      ...data,
    },
  };
}

function makeTriggerNode(): Node<TriggerNodeData> {
  return {
    id: 'trigger',
    type: 'triggerNode',
    position: { x: 250, y: 50 },
    data: {
      eventSource: 'manual',
      filters: {},
      label: 'Manual Trigger',
    },
  };
}

function makeEdge(source: string, target: string): Edge {
  return {
    id: `${source}-${target}`,
    source,
    target,
  };
}

function makeWorkflowStep(overrides: Partial<WorkflowStep> & Pick<WorkflowStep, 'id' | 'name'>): WorkflowStep {
  return {
    workflow_id: 'wf-1',
    step_number: 1,
    tool_type: 'system_tool',
    tool_id: 'ai.prompt',
    configuration: {},
    depends_on: [],
    retry_on_failure: false,
    continue_on_error: false,
    ...overrides,
  };
}

function makeWorkflowTrigger(overrides: Partial<WorkflowTrigger> & Pick<WorkflowTrigger, 'id' | 'name'>): WorkflowTrigger {
  return {
    event_source: 'document.created',
    workflow_id: 'wf-1',
    filters: {},
    workflow_inputs: null,
    enabled: true,
    priority: 1,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  };
}

// ============================================================================
// serializeWorkflow
// ============================================================================

describe('serializeWorkflow', () => {
  it('returns empty array when there are no step or conditional nodes', () => {
    const triggerNode = makeTriggerNode();
    const result = serializeWorkflow([triggerNode], []);
    expect(result).toEqual([]);
  });

  it('serializes a single step node correctly', () => {
    const step = makeStepNode('s1', {
      name: 'Extract Data',
      toolType: 'system_tool',
      toolId: 'ai.prompt',
      configuration: { model: 'claude' },
      continueOnError: true,
      thinkingMode: 'auto',
    });

    const result = serializeWorkflow([step], []);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      step_number: 1,
      name: 'Extract Data',
      tool_type: 'system_tool',
      tool_id: 'ai.prompt',
      configuration: { model: 'claude' },
      depends_on: [],
      continue_on_error: true,
      thinking_mode: 'auto',
    });
  });

  it('excludes trigger nodes from output', () => {
    const trigger = makeTriggerNode();
    const step = makeStepNode('s1');
    const result = serializeWorkflow([trigger, step], []);
    expect(result).toHaveLength(1);
    expect(result[0].name).toBe('Step s1');
  });

  it('maps edges to depends_on correctly', () => {
    const s1 = makeStepNode('s1');
    const s2 = makeStepNode('s2');
    const edge = makeEdge('s1', 's2');

    const result = serializeWorkflow([s1, s2], [edge]);

    const step2 = result.find((r) => r.name === 'Step s2');
    expect(step2?.depends_on).toContain('s1');

    const step1 = result.find((r) => r.name === 'Step s1');
    expect(step1?.depends_on).toEqual([]);
  });

  it('assigns sequential step_number values', () => {
    const s1 = makeStepNode('s1');
    const s2 = makeStepNode('s2');
    const s3 = makeStepNode('s3');
    const edge1 = makeEdge('s1', 's2');
    const edge2 = makeEdge('s2', 's3');

    const result = serializeWorkflow([s1, s2, s3], [edge1, edge2]);
    const stepNumbers = result.map((r) => r.step_number);
    // Should be [1, 2, 3] in some order (topological)
    expect(new Set(stepNumbers)).toEqual(new Set([1, 2, 3]));
  });

  it('serializes a conditional node correctly', () => {
    const cond = makeConditionalNode('c1', {
      name: 'Branch',
      condition: { field: 'status', operator: 'equals', value: 'done' },
    });

    const result = serializeWorkflow([cond], []);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      step_number: 1,
      name: 'Branch',
      tool_type: 'system_tool',
      tool_id: 'logic.conditional',
      configuration: { condition: { field: 'status', operator: 'equals', value: 'done' } },
      continue_on_error: false,
    });
  });

  it('uses default name "Condition" when conditional node name is empty', () => {
    const cond = makeConditionalNode('c1', {
      name: '',
      condition: { field: 'x', operator: 'equals', value: 1 },
    });

    const result = serializeWorkflow([cond], []);
    expect(result[0].name).toBe('Condition');
  });

  it('defaults configuration to empty object when undefined', () => {
    const step = makeStepNode('s1', { configuration: undefined });
    // Force-clear configuration to exercise the undefined-defaulting branch.
    (step.data as unknown as Record<string, unknown>).configuration = undefined;
    const result = serializeWorkflow([step], []);
    expect(result[0].configuration).toEqual({});
  });

  it('respects topological order: dependency comes before dependent', () => {
    const s1 = makeStepNode('s1');
    const s2 = makeStepNode('s2');
    const edge = makeEdge('s1', 's2');

    const result = serializeWorkflow([s2, s1], [edge]);
    const idx1 = result.findIndex((r) => r.name === 'Step s1');
    const idx2 = result.findIndex((r) => r.name === 'Step s2');
    expect(idx1).toBeLessThan(idx2);
  });

  it('handles diamond dependency (two steps converging on one)', () => {
    const s1 = makeStepNode('s1');
    const s2 = makeStepNode('s2');
    const s3 = makeStepNode('s3');
    const e1 = makeEdge('s1', 's3');
    const e2 = makeEdge('s2', 's3');

    const result = serializeWorkflow([s1, s2, s3], [e1, e2]);
    const step3 = result.find((r) => r.name === 'Step s3');
    expect(step3?.depends_on).toContain('s1');
    expect(step3?.depends_on).toContain('s2');
  });

  it('handles multiple root steps (fan-out from trigger)', () => {
    const s1 = makeStepNode('s1');
    const s2 = makeStepNode('s2');
    const s3 = makeStepNode('s3');
    const e1 = makeEdge('s1', 's3');
    const e2 = makeEdge('s2', 's3');

    const result = serializeWorkflow([s1, s2, s3], [e1, e2]);
    expect(result).toHaveLength(3);
  });
});

// ============================================================================
// deserializeWorkflow
// ============================================================================

describe('deserializeWorkflow', () => {
  it('creates a manual trigger node when no triggers provided', () => {
    const metadata = makeWorkflowMetadata();
    const { nodes } = deserializeWorkflow(metadata, []);

    const triggerNode = nodes.find((n) => n.type === 'triggerNode');
    expect(triggerNode).toBeDefined();
    expect(triggerNode?.id).toBe('trigger');
    expect((triggerNode?.data as TriggerNodeData).eventSource).toBe('manual');
    expect((triggerNode?.data as TriggerNodeData).label).toBe('Manual Trigger');
  });

  it('creates eventTriggerNode for each trigger when triggers provided', () => {
    const metadata = makeWorkflowMetadata();
    const trigger1 = makeWorkflowTrigger({ id: 't1', name: 'Trigger One' });
    const trigger2 = makeWorkflowTrigger({ id: 't2', name: 'Trigger Two' });

    const { nodes } = deserializeWorkflow(metadata, [], [trigger1, trigger2]);

    const eventNodes = nodes.filter((n) => n.type === 'eventTriggerNode');
    expect(eventNodes).toHaveLength(2);

    const ids = eventNodes.map((n) => n.id);
    expect(ids).toContain('trigger-t1');
    expect(ids).toContain('trigger-t2');
  });

  it('maps trigger data fields correctly', () => {
    const metadata = makeWorkflowMetadata();
    const trigger = makeWorkflowTrigger({
      id: 'trig-42',
      name: 'My Event',
      event_source: 'document.created',
      filters: { type: 'pdf' },
      workflow_inputs: { key: 'value' },
      enabled: false,
      priority: 5,
    });

    const { nodes } = deserializeWorkflow(metadata, [], [trigger]);
    const tNode = nodes.find((n) => n.id === 'trigger-trig-42');
    expect(tNode).toBeDefined();

    const data = tNode?.data as EventTriggerNodeData;
    expect(data.triggerId).toBe('trig-42');
    expect(data.name).toBe('My Event');
    expect(data.eventSource).toBe('document.created');
    expect(data.filters).toEqual({ type: 'pdf' });
    expect(data.workflowInputs).toEqual({ key: 'value' });
    expect(data.enabled).toBe(false);
    expect(data.priority).toBe(5);
  });

  it('converts a step to a stepNode with mapped fields', () => {
    const metadata = makeWorkflowMetadata();
    const step = makeWorkflowStep({
      id: 'step-1',
      name: 'My Step',
      description: 'Does something',
      tool_type: 'system_tool',
      tool_id: 'ai.prompt',
      configuration: { model: 'gpt-4' },
      continue_on_error: true,
      thinking_mode: 'enabled',
    });

    const { nodes } = deserializeWorkflow(metadata, [step]);
    const stepNode = nodes.find((n) => n.id === 'step-1');
    expect(stepNode).toBeDefined();
    expect(stepNode?.type).toBe('stepNode');

    const data = stepNode?.data as WorkflowStepNodeData;
    expect(data.stepId).toBe('step-1');
    expect(data.name).toBe('My Step');
    expect(data.description).toBe('Does something');
    expect(data.toolType).toBe('system_tool');
    expect(data.toolId).toBe('ai.prompt');
    expect(data.configuration).toEqual({ model: 'gpt-4' });
    expect(data.continueOnError).toBe(true);
    expect(data.thinkingMode).toBe('enabled');
  });

  it('converts a logic.conditional step to a conditionalNode', () => {
    const metadata = makeWorkflowMetadata();
    const step = makeWorkflowStep({
      id: 'cond-1',
      name: 'Branch Step',
      tool_id: 'logic.conditional',
      tool_type: 'system_tool',
      configuration: {
        condition: { field: 'status', operator: 'equals', value: 'done' },
      },
    });

    const { nodes } = deserializeWorkflow(metadata, [step]);
    const condNode = nodes.find((n) => n.id === 'cond-1');
    expect(condNode?.type).toBe('conditionalNode');

    const data = condNode?.data as ConditionalNodeData;
    expect(data.name).toBe('Branch Step');
    expect(data.condition).toEqual({ field: 'status', operator: 'equals', value: 'done' });
  });

  it('creates edges from depends_on', () => {
    const metadata = makeWorkflowMetadata();
    const step1 = makeWorkflowStep({ id: 's1', name: 'Step 1' });
    const step2 = makeWorkflowStep({ id: 's2', name: 'Step 2', depends_on: ['s1'] });

    const { edges } = deserializeWorkflow(metadata, [step1, step2]);
    const depEdge = edges.find((e) => e.source === 's1' && e.target === 's2');
    expect(depEdge).toBeDefined();
    expect(depEdge?.type).toBe('workflowEdge');
  });

  it('connects trigger node to root steps (steps with no dependencies)', () => {
    const metadata = makeWorkflowMetadata();
    const step1 = makeWorkflowStep({ id: 's1', name: 'Root Step' });
    const step2 = makeWorkflowStep({ id: 's2', name: 'Dependent Step', depends_on: ['s1'] });

    const { edges } = deserializeWorkflow(metadata, [step1, step2]);
    // Only s1 is a root step — trigger should connect to it
    const triggerEdge = edges.find((e) => e.source === 'trigger' && e.target === 's1');
    expect(triggerEdge).toBeDefined();

    // No trigger-to-s2 edge
    const badEdge = edges.find((e) => e.source === 'trigger' && e.target === 's2');
    expect(badEdge).toBeUndefined();
  });

  it('connects each event trigger node to all root steps', () => {
    const metadata = makeWorkflowMetadata();
    const trigger = makeWorkflowTrigger({ id: 'trig-a', name: 'A' });
    const step = makeWorkflowStep({ id: 's1', name: 'Root Step' });

    const { edges } = deserializeWorkflow(metadata, [step], [trigger]);
    const e = edges.find((ed) => ed.source === 'trigger-trig-a' && ed.target === 's1');
    expect(e).toBeDefined();
  });

  it('sets default condition when step has no configuration.condition', () => {
    const metadata = makeWorkflowMetadata();
    const step = makeWorkflowStep({
      id: 'cond-1',
      name: 'Branch',
      tool_id: 'logic.conditional',
      tool_type: 'system_tool',
      configuration: {},
    });

    const { nodes } = deserializeWorkflow(metadata, [step]);
    const condNode = nodes.find((n) => n.id === 'cond-1');
    const data = condNode?.data as ConditionalNodeData;
    expect(data.condition).toEqual({ field: '', operator: 'equals', value: '' });
  });

  it('returns an empty workflow with only a trigger node when steps is empty', () => {
    const metadata = makeWorkflowMetadata();
    const { nodes, edges } = deserializeWorkflow(metadata, []);

    // Should only have the manual trigger node
    expect(nodes).toHaveLength(1);
    expect(nodes[0].type).toBe('triggerNode');
    // No edges from trigger to anything
    expect(edges).toHaveLength(0);
  });

  it('assigns toolCategory from tool_id prefix', () => {
    const metadata = makeWorkflowMetadata();
    const step = makeWorkflowStep({
      id: 's1',
      name: 'A Step',
      tool_id: 'graph.search',
    });

    const { nodes } = deserializeWorkflow(metadata, [step]);
    const data = nodes.find((n) => n.id === 's1')?.data as WorkflowStepNodeData;
    expect(data.toolCategory).toBe('graph');
  });

  it('applies workflow type as conditionalNode', () => {
    const metadata = makeWorkflowMetadata();
    const step = makeWorkflowStep({
      id: 'wf-step',
      name: 'Sub Workflow',
      tool_id: 'some.workflow',
      tool_type: 'workflow',
    });

    const { nodes } = deserializeWorkflow(metadata, [step]);
    const node = nodes.find((n) => n.id === 'wf-step');
    expect(node?.type).toBe('conditionalNode');
  });
});

// ============================================================================
// Round-trip (serialize ∘ deserialize)
// ============================================================================

describe('serialize + deserialize round-trip', () => {
  it('preserves step names and tool IDs after round-trip', () => {
    const metadata = makeWorkflowMetadata();

    // Build API steps (what deserialize produces from)
    const apiStep1 = makeWorkflowStep({ id: 's1', name: 'Fetch', tool_id: 'http.get' });
    const apiStep2 = makeWorkflowStep({ id: 's2', name: 'Parse', tool_id: 'data.json', depends_on: ['s1'] });

    const { nodes, edges } = deserializeWorkflow(metadata, [apiStep1, apiStep2]);

    // Now serialize the canvas back
    const serialized = serializeWorkflow(nodes, edges);

    const names = serialized.map((s) => s.name);
    expect(names).toContain('Fetch');
    expect(names).toContain('Parse');

    const toolIds = serialized.map((s) => s.tool_id);
    expect(toolIds).toContain('http.get');
    expect(toolIds).toContain('data.json');
  });

  it('preserves dependency wiring after round-trip', () => {
    const metadata = makeWorkflowMetadata();
    const apiStep1 = makeWorkflowStep({ id: 's1', name: 'A', tool_id: 'ai.prompt' });
    const apiStep2 = makeWorkflowStep({ id: 's2', name: 'B', tool_id: 'ai.prompt', depends_on: ['s1'] });

    const { nodes, edges } = deserializeWorkflow(metadata, [apiStep1, apiStep2]);
    const serialized = serializeWorkflow(nodes, edges);

    const stepB = serialized.find((s) => s.name === 'B');
    expect(stepB?.depends_on).toContain('s1');
  });
});

// ============================================================================
// validateWorkflow
// ============================================================================

describe('validateWorkflow', () => {
  it('returns no errors for a valid single-step workflow', () => {
    const step = makeStepNode('s1');
    const trigger = makeTriggerNode();
    const edge = makeEdge('trigger', 's1');

    const errors = validateWorkflow([trigger, step], [edge]);
    expect(errors).toHaveLength(0);
  });

  it('returns error when there are no step nodes', () => {
    const trigger = makeTriggerNode();
    const errors = validateWorkflow([trigger], []);
    expect(errors.some((e) => e.message === 'Workflow must have at least one step')).toBe(true);
  });

  it('returns error for disconnected step (no edges connecting it)', () => {
    const step = makeStepNode('s1', { name: 'Orphan Step' });
    const errors = validateWorkflow([step], []);
    expect(errors.some((e) => e.nodeId === 's1' && e.message.includes('not connected'))).toBe(true);
  });

  it('returns no connection error when step appears in an edge as source', () => {
    const s1 = makeStepNode('s1', { name: 'Step One' });
    const s2 = makeStepNode('s2', { name: 'Step Two' });
    const edge = makeEdge('s1', 's2');

    const errors = validateWorkflow([s1, s2], [edge]);
    // Neither step should have a disconnection error (both appear in edges)
    const connErrors = errors.filter((e) => e.message.includes('not connected'));
    expect(connErrors).toHaveLength(0);
  });

  it('returns error for step node missing a name', () => {
    const step = makeStepNode('s1', { name: '' });
    const trigger = makeTriggerNode();
    const edge = makeEdge('trigger', 's1');

    const errors = validateWorkflow([trigger, step], [edge]);
    expect(errors.some((e) => e.nodeId === 's1' && e.field === 'name')).toBe(true);
    expect(errors.some((e) => e.message === 'Step name is required')).toBe(true);
  });

  it('returns error for step node missing a toolId', () => {
    const step = makeStepNode('s1', { toolId: '' });
    const trigger = makeTriggerNode();
    const edge = makeEdge('trigger', 's1');

    const errors = validateWorkflow([trigger, step], [edge]);
    expect(errors.some((e) => e.nodeId === 's1' && e.field === 'toolId')).toBe(true);
    expect(errors.some((e) => e.message === 'Tool selection is required')).toBe(true);
  });

  it('returns error when a cycle is detected', () => {
    const s1 = makeStepNode('s1');
    const s2 = makeStepNode('s2');
    const e1 = makeEdge('s1', 's2');
    const e2 = makeEdge('s2', 's1'); // creates a cycle

    const errors = validateWorkflow([s1, s2], [e1, e2]);
    expect(errors.some((e) => e.message.includes('cycle'))).toBe(true);
  });

  it('does not report a cycle for a valid linear workflow', () => {
    const s1 = makeStepNode('s1');
    const s2 = makeStepNode('s2');
    const s3 = makeStepNode('s3');
    const e1 = makeEdge('s1', 's2');
    const e2 = makeEdge('s2', 's3');

    const errors = validateWorkflow([s1, s2, s3], [e1, e2]);
    expect(errors.some((e) => e.message.includes('cycle'))).toBe(false);
  });

  it('does not report a cycle for a valid diamond workflow', () => {
    const s1 = makeStepNode('s1');
    const s2 = makeStepNode('s2');
    const s3 = makeStepNode('s3');
    const s4 = makeStepNode('s4');
    const e1 = makeEdge('s1', 's2');
    const e2 = makeEdge('s1', 's3');
    const e3 = makeEdge('s2', 's4');
    const e4 = makeEdge('s3', 's4');

    const errors = validateWorkflow([s1, s2, s3, s4], [e1, e2, e3, e4]);
    expect(errors.some((e) => e.message.includes('cycle'))).toBe(false);
  });

  it('does not check name/toolId on conditional nodes', () => {
    const cond = makeConditionalNode('c1');
    const trigger = makeTriggerNode();
    const edge = makeEdge('trigger', 'c1');

    const errors = validateWorkflow([trigger, cond], [edge]);
    // Conditional node should not trigger name/toolId errors
    const fieldErrors = errors.filter((e) => e.field === 'name' || e.field === 'toolId');
    expect(fieldErrors).toHaveLength(0);
  });

  it('accumulates multiple errors independently', () => {
    // Two disconnected step nodes, both with missing name and toolId
    const s1 = makeStepNode('s1', { name: '', toolId: '' });
    const s2 = makeStepNode('s2', { name: '', toolId: '' });

    const errors = validateWorkflow([s1, s2], []);
    // At least 2 disconnection errors + 2 name errors + 2 toolId errors
    expect(errors.length).toBeGreaterThanOrEqual(4);
  });

  it('handles empty workflow gracefully', () => {
    const errors = validateWorkflow([], []);
    expect(errors.some((e) => e.message === 'Workflow must have at least one step')).toBe(true);
  });

  it('self-loop edge is treated as a cycle', () => {
    const s1 = makeStepNode('s1');
    const selfLoop = makeEdge('s1', 's1');

    const errors = validateWorkflow([s1], [selfLoop]);
    expect(errors.some((e) => e.message.includes('cycle'))).toBe(true);
  });

  it('trigger-only workflow (no step nodes) returns step-required error', () => {
    const trigger = makeTriggerNode();
    const errors = validateWorkflow([trigger], []);
    expect(errors.some((e) => e.message === 'Workflow must have at least one step')).toBe(true);
  });

  it('returns no errors for multiple triggers with a valid connected step', () => {
    // Simulate two event trigger nodes + one step node, all edges present
    const trig1: Node = {
      id: 'trigger-t1',
      type: 'eventTriggerNode',
      position: { x: 0, y: 0 },
      data: { triggerId: 't1', name: 'T1', eventSource: 'doc', filters: {}, workflowInputs: null, enabled: true, priority: 1 },
    };
    const trig2: Node = {
      id: 'trigger-t2',
      type: 'eventTriggerNode',
      position: { x: 200, y: 0 },
      data: { triggerId: 't2', name: 'T2', eventSource: 'doc', filters: {}, workflowInputs: null, enabled: true, priority: 2 },
    };
    const step = makeStepNode('s1', { name: 'A Step', toolId: 'ai.prompt' });

    const edges: Edge[] = [
      makeEdge('trigger-t1', 's1'),
      makeEdge('trigger-t2', 's1'),
    ];

    const errors = validateWorkflow([trig1, trig2, step], edges);
    expect(errors).toHaveLength(0);
  });
});
