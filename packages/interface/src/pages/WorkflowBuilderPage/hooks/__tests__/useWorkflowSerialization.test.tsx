// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useWorkflowSerialization — workflow save/load/validate hook.
 *
 * Strategy:
 * - Mock workflowsApi and triggersApi with vi.fn() stubs.
 * - Use the REAL serializeWorkflow / deserializeWorkflow / validateWorkflow.
 * - Build synthetic @xyflow/react Node/Edge fixtures.
 * - renderHook + act + waitFor from @testing-library/react.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import type { Node, Edge } from '@xyflow/react';
import type { Workflow, WorkflowStep, WorkflowTrigger } from '../../../../services/api/workflows';
import type { WorkflowStepNodeData, EventTriggerNodeData } from '../../types';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../../../../services/api/workflows', () => ({
  workflowsApi: {
    get: vi.fn<(workflowId: string) => Promise<Workflow>>(),
    create: vi.fn<(data: Record<string, unknown>) => Promise<Workflow>>(),
    update: vi.fn<(workflowId: string, data: Record<string, unknown>) => Promise<Workflow>>(),
    listSteps: vi.fn<(workflowId: string) => Promise<WorkflowStep[]>>(),
    createStep: vi.fn<(workflowId: string, data: Record<string, unknown>) => Promise<WorkflowStep>>(),
    updateStep: vi.fn<(workflowId: string, stepId: string, data: Record<string, unknown>) => Promise<WorkflowStep>>(),
    deleteStep: vi.fn<(workflowId: string, stepId: string) => Promise<void>>(),
    reorderSteps: vi.fn<(workflowId: string, stepOrder: string[]) => Promise<void>>(),
    listTriggers: vi.fn<(workflowId: string) => Promise<WorkflowTrigger[]>>(),
  },
}));

vi.mock('../../../../services/api/triggers', () => ({
  triggersApi: {
    create: vi.fn<(data: Record<string, unknown>) => Promise<Record<string, unknown>>>(),
    update: vi.fn<(triggerId: string, data: Record<string, unknown>) => Promise<Record<string, unknown>>>(),
    delete: vi.fn<(triggerId: string) => Promise<void>>(),
  },
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeApiWorkflow(overrides?: Partial<Workflow>): Workflow {
  return {
    id: 'wf-1',
    database_name: 'default',
    name: 'Test Workflow',
    description: 'A test workflow',
    is_system: false,
    is_active: true,
    expose_as_ai_tool: false,
    input_schema: {},
    tags: [],
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
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

function makeStepNode(id: string, data?: Partial<WorkflowStepNodeData>): Node<WorkflowStepNodeData> {
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

function makeEventTriggerNode(id: string, data?: Partial<EventTriggerNodeData>): Node<EventTriggerNodeData> {
  return {
    id: `trigger-${id}`,
    type: 'eventTriggerNode',
    position: { x: 0, y: 0 },
    data: {
      triggerId: id,
      name: `Trigger ${id}`,
      eventSource: 'document.created',
      filters: {},
      workflowInputs: null,
      enabled: true,
      priority: 1,
      ...data,
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

// ---------------------------------------------------------------------------
// Valid canvas: one connected step node
// ---------------------------------------------------------------------------

function makeValidCanvas(): { nodes: Node[]; edges: Edge[] } {
  const trigger: Node = {
    id: 'trigger',
    type: 'triggerNode',
    position: { x: 250, y: 50 },
    data: { eventSource: 'manual', filters: {}, label: 'Manual Trigger' },
  };
  const step = makeStepNode('s1');
  const edge = makeEdge('trigger', 's1');
  return { nodes: [trigger, step], edges: [edge] };
}

// ---------------------------------------------------------------------------
// Import helper (ensures mocks are in place before importing the hook)
// ---------------------------------------------------------------------------

async function importHook() {
  const { workflowsApi } = await import('../../../../services/api/workflows');
  const { triggersApi } = await import('../../../../services/api/triggers');
  const { useWorkflowSerialization } = await import('../useWorkflowSerialization');
  return { workflowsApi, triggersApi, useWorkflowSerialization };
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

// ===========================================================================
// Suite: initial state
// ===========================================================================

describe('useWorkflowSerialization — initial state', () => {
  it('workflow starts null', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { result } = renderHook(() => useWorkflowSerialization());
    expect(result.current.workflow).toBeNull();
  });

  it('isLoading starts false', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { result } = renderHook(() => useWorkflowSerialization());
    expect(result.current.isLoading).toBe(false);
  });

  it('isSaving starts false', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { result } = renderHook(() => useWorkflowSerialization());
    expect(result.current.isSaving).toBe(false);
  });

  it('error starts null', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { result } = renderHook(() => useWorkflowSerialization());
    expect(result.current.error).toBeNull();
  });

  it('validationErrors starts as empty array', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { result } = renderHook(() => useWorkflowSerialization());
    expect(result.current.validationErrors).toEqual([]);
  });

  it('exposes all action functions', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { result } = renderHook(() => useWorkflowSerialization());
    expect(typeof result.current.loadWorkflow).toBe('function');
    expect(typeof result.current.saveWorkflow).toBe('function');
    expect(typeof result.current.createWorkflow).toBe('function');
    expect(typeof result.current.validate).toBe('function');
  });
});

// ===========================================================================
// Suite: validate
// ===========================================================================

describe('useWorkflowSerialization — validate', () => {
  it('returns no errors for a valid workflow', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    const { result } = renderHook(() => useWorkflowSerialization());

    let errors!: ReturnType<typeof result.current.validate>;
    act(() => {
      errors = result.current.validate(nodes, edges);
    });

    expect(errors).toHaveLength(0);
    expect(result.current.validationErrors).toHaveLength(0);
  });

  it('returns errors for an empty node list', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { result } = renderHook(() => useWorkflowSerialization());

    let errors!: ReturnType<typeof result.current.validate>;
    act(() => {
      errors = result.current.validate([], []);
    });

    expect(errors.length).toBeGreaterThan(0);
    expect(errors[0].message).toBe('Workflow must have at least one step');
  });

  it('populates validationErrors state', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { result } = renderHook(() => useWorkflowSerialization());

    act(() => {
      result.current.validate([], []);
    });

    expect(result.current.validationErrors.length).toBeGreaterThan(0);
  });

  it('returns errors for a disconnected step', async () => {
    const { useWorkflowSerialization } = await importHook();
    const step = makeStepNode('s1');
    const { result } = renderHook(() => useWorkflowSerialization());

    let errors!: ReturnType<typeof result.current.validate>;
    act(() => {
      errors = result.current.validate([step], []);
    });

    expect(errors.some((e) => e.nodeId === 's1' && e.message.includes('not connected'))).toBe(true);
  });

  it('clears validationErrors when called with valid canvas', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    const { result } = renderHook(() => useWorkflowSerialization());

    // First call: invalid
    act(() => {
      result.current.validate([], []);
    });
    expect(result.current.validationErrors.length).toBeGreaterThan(0);

    // Second call: valid
    act(() => {
      result.current.validate(nodes, edges);
    });
    expect(result.current.validationErrors).toHaveLength(0);
  });
});

// ===========================================================================
// Suite: loadWorkflow
// ===========================================================================

describe('useWorkflowSerialization — loadWorkflow', () => {
  it('calls workflowsApi.get with the provided workflowId', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const apiWorkflow = makeApiWorkflow();
    (workflowsApi.get as Mock).mockResolvedValue(apiWorkflow);
    (workflowsApi.listSteps as Mock).mockResolvedValue([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    expect(workflowsApi.get).toHaveBeenCalledWith('wf-1');
  });

  it('calls workflowsApi.listSteps and listTriggers in parallel', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow());
    (workflowsApi.listSteps as Mock).mockResolvedValue([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    expect(workflowsApi.listSteps).toHaveBeenCalledWith('wf-1');
    expect(workflowsApi.listTriggers).toHaveBeenCalledWith('wf-1');
  });

  it('sets workflow state after successful load', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const apiWorkflow = makeApiWorkflow({ id: 'wf-42', name: 'My Workflow' });
    (workflowsApi.get as Mock).mockResolvedValue(apiWorkflow);
    (workflowsApi.listSteps as Mock).mockResolvedValue([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-42');
    });

    expect(result.current.workflow).not.toBeNull();
    expect(result.current.workflow?.id).toBe('wf-42');
    expect(result.current.workflow?.name).toBe('My Workflow');
  });

  it('returns nodes and edges for a workflow with steps', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const apiWorkflow = makeApiWorkflow();
    const step = makeWorkflowStep({ id: 's1', name: 'Fetch Data' });
    (workflowsApi.get as Mock).mockResolvedValue(apiWorkflow);
    (workflowsApi.listSteps as Mock).mockResolvedValue([step]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    let canvasResult: { nodes: Node[]; edges: Edge[] } | null = null;
    await act(async () => {
      canvasResult = await result.current.loadWorkflow('wf-1');
    });

    expect(canvasResult).not.toBeNull();
    expect(canvasResult!.nodes.some((n) => n.id === 's1')).toBe(true);
  });

  it('isLoading is true while loading, false after', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    let resolveGet!: (v: Workflow) => void;
    (workflowsApi.get as Mock).mockReturnValue(new Promise<Workflow>((res) => { resolveGet = res; }));
    (workflowsApi.listSteps as Mock).mockResolvedValue([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    act(() => {
      void result.current.loadWorkflow('wf-1');
    });

    expect(result.current.isLoading).toBe(true);

    await act(async () => {
      resolveGet(makeApiWorkflow());
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
  });

  it('sets error and calls onError when workflowsApi.get rejects', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    (workflowsApi.get as Mock).mockRejectedValue(new Error('Not found'));
    (workflowsApi.listSteps as Mock).mockResolvedValue([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const onError = vi.fn<(msg: string) => void>();
    const { result } = renderHook(() => useWorkflowSerialization({ onError }));

    await act(async () => {
      await result.current.loadWorkflow('wf-999');
    });

    expect(result.current.error).toBe('Not found');
    expect(onError).toHaveBeenCalledWith('Not found');
  });

  it('returns null on load error', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    (workflowsApi.get as Mock).mockRejectedValue(new Error('Server error'));
    (workflowsApi.listSteps as Mock).mockResolvedValue([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    let canvasResult: { nodes: Node[]; edges: Edge[] } | null = undefined as unknown as null;
    await act(async () => {
      canvasResult = await result.current.loadWorkflow('wf-1');
    });

    expect(canvasResult).toBeNull();
  });

  it('uses generic error message when thrown value is not an Error', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    (workflowsApi.get as Mock).mockRejectedValue('plain string');
    (workflowsApi.listSteps as Mock).mockResolvedValue([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    expect(result.current.error).toBe('Failed to load workflow');
  });

  it('handles triggers failure gracefully (listTriggers rejection)', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow());
    (workflowsApi.listSteps as Mock).mockResolvedValue([]);
    // listTriggers rejects — hook uses .catch(() => []) so this should not throw
    (workflowsApi.listTriggers as Mock).mockRejectedValue(new Error('Triggers unavailable'));

    const { result } = renderHook(() => useWorkflowSerialization());

    let canvasResult: { nodes: Node[]; edges: Edge[] } | null = null;
    await act(async () => {
      canvasResult = await result.current.loadWorkflow('wf-1');
    });

    // Should still succeed with the manual trigger node
    expect(canvasResult).not.toBeNull();
    expect(result.current.error).toBeNull();
  });

  it('deserializes trigger nodes when triggers are returned', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const trigger = makeWorkflowTrigger({ id: 't1', name: 'Event Trigger' });
    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow());
    (workflowsApi.listSteps as Mock).mockResolvedValue([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([trigger]);

    const { result } = renderHook(() => useWorkflowSerialization());

    let canvasResult: { nodes: Node[]; edges: Edge[] } | null = null;
    await act(async () => {
      canvasResult = await result.current.loadWorkflow('wf-1');
    });

    const eventTriggerNode = canvasResult!.nodes.find((n) => n.type === 'eventTriggerNode');
    expect(eventTriggerNode).toBeDefined();
    expect(eventTriggerNode?.id).toBe('trigger-t1');
  });
});

// ===========================================================================
// Suite: createWorkflow
// ===========================================================================

describe('useWorkflowSerialization — createWorkflow', () => {
  it('returns null if validation fails (no steps)', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { result } = renderHook(() => useWorkflowSerialization());

    let returnValue: string | null = undefined as unknown as null;
    await act(async () => {
      returnValue = await result.current.createWorkflow([], [], { name: 'Test' });
    });

    expect(returnValue).toBeNull();
  });

  it('sets validationErrors and calls onError when validation fails', async () => {
    const { useWorkflowSerialization } = await importHook();
    const onError = vi.fn<(msg: string) => void>();
    const { result } = renderHook(() => useWorkflowSerialization({ onError }));

    await act(async () => {
      await result.current.createWorkflow([], [], { name: 'Test' });
    });

    expect(result.current.validationErrors.length).toBeGreaterThan(0);
    expect(onError).toHaveBeenCalled();
  });

  it('calls workflowsApi.create with metadata on valid canvas', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    const newWorkflow = makeApiWorkflow({ id: 'wf-new', name: 'My New Workflow' });
    (workflowsApi.create as Mock).mockResolvedValue(newWorkflow);
    (workflowsApi.createStep as Mock).mockResolvedValue({});
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.createWorkflow(nodes, edges, { name: 'My New Workflow', description: 'Desc' });
    });

    expect(workflowsApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'My New Workflow', description: 'Desc' })
    );
  });

  it('returns the new workflow id on success', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    (workflowsApi.create as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-created' }));
    (workflowsApi.createStep as Mock).mockResolvedValue({});
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    let returnedId: string | null = null;
    await act(async () => {
      returnedId = await result.current.createWorkflow(nodes, edges, { name: 'X' });
    });

    expect(returnedId).toBe('wf-created');
  });

  it('calls workflowsApi.createStep for each serialized step', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    (workflowsApi.create as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-new' }));
    (workflowsApi.createStep as Mock).mockResolvedValue({});
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.createWorkflow(nodes, edges, { name: 'X' });
    });

    // One step node in the canvas → one createStep call
    expect(workflowsApi.createStep).toHaveBeenCalledTimes(1);
    expect(workflowsApi.createStep).toHaveBeenCalledWith('wf-new', expect.objectContaining({ name: 'Step s1' }));
  });

  it('sets workflow state after create', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    const newWorkflow = makeApiWorkflow({ id: 'wf-fresh' });
    (workflowsApi.create as Mock).mockResolvedValue(newWorkflow);
    (workflowsApi.createStep as Mock).mockResolvedValue({});
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.createWorkflow(nodes, edges, { name: 'X' });
    });

    expect(result.current.workflow?.id).toBe('wf-fresh');
  });

  it('calls onSuccess after create', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    const onSuccess = vi.fn<(msg: string) => void>();
    (workflowsApi.create as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-new' }));
    (workflowsApi.createStep as Mock).mockResolvedValue({});
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization({ onSuccess }));

    await act(async () => {
      await result.current.createWorkflow(nodes, edges, { name: 'X' });
    });

    expect(onSuccess).toHaveBeenCalledWith('Workflow created successfully');
  });

  it('creates triggers via triggersApi.create for eventTriggerNodes', async () => {
    const { useWorkflowSerialization, workflowsApi, triggersApi } = await importHook();

    const triggerNode = makeEventTriggerNode('t-new', { triggerId: null });
    const step = makeStepNode('s1');
    const edge = makeEdge('trigger-t-new', 's1');
    const nodes = [triggerNode, step];
    const edges = [edge];

    (workflowsApi.create as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-new' }));
    (workflowsApi.createStep as Mock).mockResolvedValue({});
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);
    (triggersApi.create as Mock).mockResolvedValue({});

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.createWorkflow(nodes, edges, { name: 'X' });
    });

    expect(triggersApi.create).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Trigger t-new',
        workflow_id: 'wf-new',
      })
    );
  });

  it('sets error and returns null when workflowsApi.create rejects', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    (workflowsApi.create as Mock).mockRejectedValue(new Error('Create failed'));

    const onError = vi.fn<(msg: string) => void>();
    const { result } = renderHook(() => useWorkflowSerialization({ onError }));

    let returnValue: string | null = undefined as unknown as null;
    await act(async () => {
      returnValue = await result.current.createWorkflow(nodes, edges, { name: 'X' });
    });

    expect(returnValue).toBeNull();
    expect(result.current.error).toBe('Create failed');
    expect(onError).toHaveBeenCalledWith('Create failed');
  });

  it('uses generic error message when thrown value is not an Error', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    (workflowsApi.create as Mock).mockRejectedValue('string error');

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.createWorkflow(nodes, edges, { name: 'X' });
    });

    expect(result.current.error).toBe('Failed to create workflow');
  });

  it('isSaving is true during create, false after', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    let resolveCreate!: (v: Workflow) => void;
    (workflowsApi.create as Mock).mockReturnValue(new Promise<Workflow>((res) => { resolveCreate = res; }));

    const { result } = renderHook(() => useWorkflowSerialization());

    act(() => {
      void result.current.createWorkflow(nodes, edges, { name: 'X' });
    });

    expect(result.current.isSaving).toBe(true);

    await act(async () => {
      resolveCreate(makeApiWorkflow({ id: 'wf-new' }));
    });

    await waitFor(() => {
      expect(result.current.isSaving).toBe(false);
    });
  });

  it('defaults name to "New Workflow" when metadata.name is empty', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    (workflowsApi.create as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-new' }));
    (workflowsApi.createStep as Mock).mockResolvedValue({});
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.createWorkflow(nodes, edges, {});
    });

    expect(workflowsApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'New Workflow' })
    );
  });
});

// ===========================================================================
// Suite: saveWorkflow
// ===========================================================================

describe('useWorkflowSerialization — saveWorkflow', () => {
  it('returns null and sets error if no workflow is loaded', async () => {
    const { useWorkflowSerialization } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    const { result } = renderHook(() => useWorkflowSerialization());

    let returnValue: string | null = undefined as unknown as null;
    await act(async () => {
      returnValue = await result.current.saveWorkflow(nodes, edges, { name: 'X' });
    });

    expect(returnValue).toBeNull();
    expect(result.current.error).toBe('No workflow loaded');
  });

  it('returns null and sets validationErrors if validation fails', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    // First load a workflow so workflow.id is set
    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock).mockResolvedValue([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const onError = vi.fn<(msg: string) => void>();
    const { result } = renderHook(() => useWorkflowSerialization({ onError }));

    // Load workflow
    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    // Now attempt save with invalid canvas
    let returnValue: string | null = undefined as unknown as null;
    await act(async () => {
      returnValue = await result.current.saveWorkflow([], [], { name: 'X' });
    });

    expect(returnValue).toBeNull();
    expect(result.current.validationErrors.length).toBeGreaterThan(0);
    expect(onError).toHaveBeenCalled();
  });

  it('calls workflowsApi.update with metadata', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();

    // Load a workflow first
    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock)
      .mockResolvedValueOnce([]) // load
      .mockResolvedValueOnce([]) // save: get existing steps
      .mockResolvedValueOnce([]); // save: list after create
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);
    (workflowsApi.update as Mock).mockResolvedValue(makeApiWorkflow());
    (workflowsApi.createStep as Mock).mockResolvedValue({ id: 'new-step', step_number: 1 });
    (workflowsApi.reorderSteps as Mock).mockResolvedValue(undefined);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    await act(async () => {
      await result.current.saveWorkflow(nodes, edges, { name: 'Updated Name', description: 'New desc' });
    });

    expect(workflowsApi.update).toHaveBeenCalledWith('wf-1', expect.objectContaining({ name: 'Updated Name' }));
  });

  it('returns workflow id on successful save', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();

    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);
    (workflowsApi.update as Mock).mockResolvedValue(makeApiWorkflow());
    (workflowsApi.createStep as Mock).mockResolvedValue({ id: 'new-step', step_number: 1 });
    (workflowsApi.reorderSteps as Mock).mockResolvedValue(undefined);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    let returnValue: string | null = null;
    await act(async () => {
      returnValue = await result.current.saveWorkflow(nodes, edges, { name: 'X' });
    });

    expect(returnValue).toBe('wf-1');
  });

  it('calls onSuccess after save', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    const onSuccess = vi.fn<(msg: string) => void>();

    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);
    (workflowsApi.update as Mock).mockResolvedValue(makeApiWorkflow());
    (workflowsApi.createStep as Mock).mockResolvedValue({ id: 'new-step', step_number: 1 });
    (workflowsApi.reorderSteps as Mock).mockResolvedValue(undefined);

    const { result } = renderHook(() => useWorkflowSerialization({ onSuccess }));

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    await act(async () => {
      await result.current.saveWorkflow(nodes, edges, { name: 'X' });
    });

    expect(onSuccess).toHaveBeenCalledWith('Workflow saved successfully');
  });

  it('deletes removed steps (steps no longer in nodes)', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();

    const existingStep = makeWorkflowStep({ id: 'old-step', name: 'Old Step' });

    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock)
      .mockResolvedValueOnce([]) // initial load
      .mockResolvedValueOnce([existingStep]) // during save: get existing steps
      .mockResolvedValueOnce([]); // after step ops: reorder
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);
    (workflowsApi.update as Mock).mockResolvedValue(makeApiWorkflow());
    (workflowsApi.deleteStep as Mock).mockResolvedValue(undefined);
    (workflowsApi.createStep as Mock).mockResolvedValue({ id: 'new-step', step_number: 1 });
    (workflowsApi.reorderSteps as Mock).mockResolvedValue(undefined);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    await act(async () => {
      await result.current.saveWorkflow(nodes, edges, { name: 'X' });
    });

    // 'old-step' is not referenced by any node so it should be deleted
    expect(workflowsApi.deleteStep).toHaveBeenCalledWith('wf-1', 'old-step');
  });

  it('calls triggersApi.delete for removed triggers', async () => {
    const { useWorkflowSerialization, workflowsApi, triggersApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();

    const existingTrigger = makeWorkflowTrigger({ id: 'old-trig', name: 'Old Trigger' });

    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);
    // listTriggers: load + during save
    (workflowsApi.listTriggers as Mock)
      .mockResolvedValueOnce([]) // load
      .mockResolvedValueOnce([existingTrigger]); // save: existing triggers
    (workflowsApi.update as Mock).mockResolvedValue(makeApiWorkflow());
    (workflowsApi.createStep as Mock).mockResolvedValue({ id: 'new-step', step_number: 1 });
    (workflowsApi.reorderSteps as Mock).mockResolvedValue(undefined);
    (triggersApi.delete as Mock).mockResolvedValue(undefined);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    // nodes does NOT include an eventTriggerNode for 'old-trig'
    await act(async () => {
      await result.current.saveWorkflow(nodes, edges, { name: 'X' });
    });

    expect(triggersApi.delete).toHaveBeenCalledWith('old-trig');
  });

  it('calls triggersApi.create for new trigger nodes (no triggerId)', async () => {
    const { useWorkflowSerialization, workflowsApi, triggersApi } = await importHook();

    const newTriggerNode = makeEventTriggerNode('temp', { triggerId: null });
    const step = makeStepNode('s1');
    const edge = makeEdge('trigger-temp', 's1');
    const nodes = [newTriggerNode, step];
    const edges = [edge];

    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);
    (workflowsApi.listTriggers as Mock)
      .mockResolvedValueOnce([]) // load
      .mockResolvedValueOnce([]); // save: existing triggers
    (workflowsApi.update as Mock).mockResolvedValue(makeApiWorkflow());
    (workflowsApi.createStep as Mock).mockResolvedValue({ id: 'new-step', step_number: 1 });
    (workflowsApi.reorderSteps as Mock).mockResolvedValue(undefined);
    (triggersApi.create as Mock).mockResolvedValue({});

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    await act(async () => {
      await result.current.saveWorkflow(nodes, edges, { name: 'X' });
    });

    expect(triggersApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ workflow_id: 'wf-1' })
    );
  });

  it('calls triggersApi.update for existing trigger nodes', async () => {
    const { useWorkflowSerialization, workflowsApi, triggersApi } = await importHook();

    const existingTrigger = makeWorkflowTrigger({ id: 'trig-1', name: 'My Trigger' });
    const triggerNode = makeEventTriggerNode('existing', {
      triggerId: 'trig-1',
      name: 'My Trigger Updated',
      eventSource: 'document.created',
    });
    const step = makeStepNode('s1');
    const edge = makeEdge('trigger-existing', 's1');
    const nodes = [triggerNode, step];
    const edges = [edge];

    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);
    (workflowsApi.listTriggers as Mock)
      .mockResolvedValueOnce([]) // load
      .mockResolvedValueOnce([existingTrigger]); // save: existing triggers
    (workflowsApi.update as Mock).mockResolvedValue(makeApiWorkflow());
    (workflowsApi.createStep as Mock).mockResolvedValue({ id: 'new-step', step_number: 1 });
    (workflowsApi.reorderSteps as Mock).mockResolvedValue(undefined);
    (triggersApi.update as Mock).mockResolvedValue({});

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    await act(async () => {
      await result.current.saveWorkflow(nodes, edges, { name: 'X' });
    });

    expect(triggersApi.update).toHaveBeenCalledWith(
      'trig-1',
      expect.objectContaining({ name: 'My Trigger Updated' })
    );
  });

  it('sets error and returns null when workflowsApi.update rejects', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    const onError = vi.fn<(msg: string) => void>();

    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);
    (workflowsApi.update as Mock).mockRejectedValue(new Error('Update failed'));

    const { result } = renderHook(() => useWorkflowSerialization({ onError }));

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    let returnValue: string | null = undefined as unknown as null;
    await act(async () => {
      returnValue = await result.current.saveWorkflow(nodes, edges, { name: 'X' });
    });

    expect(returnValue).toBeNull();
    expect(result.current.error).toBe('Update failed');
    expect(onError).toHaveBeenCalledWith('Update failed');
  });

  it('uses generic error message when thrown value is not an Error', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();

    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);
    (workflowsApi.update as Mock).mockRejectedValue('not an error');

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    await act(async () => {
      await result.current.saveWorkflow(nodes, edges, { name: 'X' });
    });

    expect(result.current.error).toBe('Failed to save workflow');
  });

  it('isSaving is false after save error', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();

    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);
    (workflowsApi.update as Mock).mockRejectedValue(new Error('Fail'));

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    await act(async () => {
      await result.current.saveWorkflow(nodes, edges, { name: 'X' });
    });

    expect(result.current.isSaving).toBe(false);
  });

  it('calls workflowsApi.reorderSteps after step sync', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();

    (workflowsApi.get as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-1' }));
    (workflowsApi.listSteps as Mock)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ id: 'new-step', step_number: 1 }]);
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);
    (workflowsApi.update as Mock).mockResolvedValue(makeApiWorkflow());
    (workflowsApi.createStep as Mock).mockResolvedValue({ id: 'new-step', step_number: 1 });
    (workflowsApi.reorderSteps as Mock).mockResolvedValue(undefined);

    const { result } = renderHook(() => useWorkflowSerialization());

    await act(async () => {
      await result.current.loadWorkflow('wf-1');
    });

    await act(async () => {
      await result.current.saveWorkflow(nodes, edges, { name: 'X' });
    });

    expect(workflowsApi.reorderSteps).toHaveBeenCalledWith('wf-1', expect.any(Array));
  });
});

// ===========================================================================
// Suite: options callbacks
// ===========================================================================

describe('useWorkflowSerialization — options callbacks', () => {
  it('onError is not called when there is no error', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    const onError = vi.fn<(msg: string) => void>();
    (workflowsApi.create as Mock).mockResolvedValue(makeApiWorkflow({ id: 'wf-new' }));
    (workflowsApi.createStep as Mock).mockResolvedValue({});
    (workflowsApi.listTriggers as Mock).mockResolvedValue([]);

    const { result } = renderHook(() => useWorkflowSerialization({ onError }));

    await act(async () => {
      await result.current.createWorkflow(nodes, edges, { name: 'X' });
    });

    expect(onError).not.toHaveBeenCalled();
  });

  it('onSuccess is not called when creation fails', async () => {
    const { useWorkflowSerialization, workflowsApi } = await importHook();
    const { nodes, edges } = makeValidCanvas();
    const onSuccess = vi.fn<(msg: string) => void>();
    (workflowsApi.create as Mock).mockRejectedValue(new Error('fail'));

    const { result } = renderHook(() => useWorkflowSerialization({ onSuccess }));

    await act(async () => {
      await result.current.createWorkflow(nodes, edges, { name: 'X' });
    });

    expect(onSuccess).not.toHaveBeenCalled();
  });
});
