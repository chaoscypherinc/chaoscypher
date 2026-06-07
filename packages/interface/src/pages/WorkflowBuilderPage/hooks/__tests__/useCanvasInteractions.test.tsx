// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useCanvasInteractions — canvas interaction handlers hook.
 *
 * Strategy:
 * - Mock @xyflow/react to supply useReactFlow (screenToFlowPosition) and addEdge.
 * - Mock logger.
 * - Use renderHook + act from @testing-library/react.
 * - Construct minimal synthetic DragEvent / Connection objects cast through unknown.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { Node, Edge, Connection, NodeChange, EdgeChange } from '@xyflow/react';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockScreenToFlowPosition = vi.fn<(pos: { x: number; y: number }) => { x: number; y: number }>(
  () => ({ x: 100, y: 200 }),
);

vi.mock('@xyflow/react', () => ({
  useReactFlow: () => ({
    screenToFlowPosition: mockScreenToFlowPosition,
  }),
  addEdge: vi.fn<(connection: Connection, edges: Edge[]) => Edge[]>(
    (connection, edges) => [
      ...edges,
      {
        ...connection,
        id: `${connection.source ?? 'src'}-${connection.target ?? 'tgt'}`,
        source: connection.source ?? 'src',
        target: connection.target ?? 'tgt',
      } as Edge,
    ],
  ),
}));

vi.mock('../../../../utils/logger', () => ({
  logger: {
    error: vi.fn<(msg: string, ...args: unknown[]) => void>(),
    info: vi.fn<(msg: string, ...args: unknown[]) => void>(),
    warn: vi.fn<(msg: string, ...args: unknown[]) => void>(),
  },
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeNode(id: string, type = 'stepNode'): Node {
  return { id, type, position: { x: 0, y: 0 }, data: {} };
}

function makeEdge(id: string, source: string, target: string): Edge {
  return { id, source, target };
}

/** Minimal DragEvent over an HTMLDivElement */
function makeDragEvent(overrides?: {
  clientX?: number;
  clientY?: number;
  toolData?: string;
  inputData?: string;
  triggerData?: string;
}): React.DragEvent<HTMLDivElement> {
  const getData = vi.fn<(type: string) => string>((type: string) => {
    if (type === 'application/workflow-tool') return overrides?.toolData ?? '';
    if (type === 'application/workflow-input') return overrides?.inputData ?? '';
    if (type === 'application/workflow-trigger') return overrides?.triggerData ?? '';
    return '';
  });

  const dataTransfer = { getData, dropEffect: '' } as unknown as DataTransfer;

  return {
    preventDefault: vi.fn<() => void>(),
    clientX: overrides?.clientX ?? 0,
    clientY: overrides?.clientY ?? 0,
    dataTransfer,
  } as unknown as React.DragEvent<HTMLDivElement>;
}

/** Minimal SystemTool payload */
const sampleTool = {
  id: 'ai.prompt',
  name: 'AI Prompt',
  description: 'AI prompt tool',
  category: 'ai',
  input_schema: {},
  output_schema: {},
  version: '1.0',
  is_active: true,
};

/** Minimal event trigger payload */
const sampleTrigger = {
  eventSource: 'document.created',
  name: 'Doc Trigger',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function importHook() {
  const { useCanvasInteractions } = await import('../useCanvasInteractions');
  return { useCanvasInteractions };
}

function makeHookArgs(nodeOverrides?: Node[]) {
  const nodes: Node[] = nodeOverrides ?? [];
  const setNodes = vi.fn<React.Dispatch<React.SetStateAction<Node[]>>>();
  const setEdges = vi.fn<React.Dispatch<React.SetStateAction<Edge[]>>>();
  const onNodesChange = vi.fn<(changes: NodeChange[]) => void>();
  const onEdgesChange = vi.fn<(changes: EdgeChange[]) => void>();
  const pushUndoState = vi.fn<() => void>();
  const markDirty = vi.fn<() => void>();
  return { nodes, setNodes, setEdges, onNodesChange, onEdgesChange, pushUndoState, markDirty };
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  mockScreenToFlowPosition.mockReturnValue({ x: 100, y: 200 });
});

// ===========================================================================
// Suite: initial state
// ===========================================================================

describe('useCanvasInteractions — initial state', () => {
  it('selectedNode starts null', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );
    expect(result.current.selectedNode).toBeNull();
  });

  it('selectedEdge starts null', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );
    expect(result.current.selectedEdge).toBeNull();
  });

  it('isPropertiesPanelOpen starts false', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );
    expect(result.current.isPropertiesPanelOpen).toBe(false);
  });

  it('isDragOver starts false', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );
    expect(result.current.isDragOver).toBe(false);
  });

  it('exposes all handler functions', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );
    const handlers = [
      'handleNodeClick', 'handleEdgeClick', 'handlePaneClick',
      'onConnect', 'handleNodesChange', 'handleEdgesChange',
      'handleNodeUpdate', 'onDrop', 'onDragOver', 'onDragLeave',
      'deleteSelectedNode', 'deleteSelectedEdge',
    ] as const;
    for (const h of handlers) {
      expect(typeof result.current[h]).toBe('function');
    }
  });
});

// ===========================================================================
// Suite: selection handlers
// ===========================================================================

describe('useCanvasInteractions — selection', () => {
  it('handleNodeClick sets selectedNode and opens panel', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const node = makeNode('n1');
    act(() => {
      result.current.handleNodeClick({} as React.MouseEvent, node);
    });

    expect(result.current.selectedNode).toEqual(node);
    expect(result.current.selectedEdge).toBeNull();
    expect(result.current.isPropertiesPanelOpen).toBe(true);
  });

  it('handleEdgeClick sets selectedEdge and opens panel', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const edge = makeEdge('e1', 'n1', 'n2');
    act(() => {
      result.current.handleEdgeClick({} as React.MouseEvent, edge);
    });

    expect(result.current.selectedEdge).toEqual(edge);
    expect(result.current.selectedNode).toBeNull();
    expect(result.current.isPropertiesPanelOpen).toBe(true);
  });

  it('handlePaneClick clears selection and closes panel', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const node = makeNode('n1');
    act(() => {
      result.current.handleNodeClick({} as React.MouseEvent, node);
    });
    expect(result.current.isPropertiesPanelOpen).toBe(true);

    act(() => {
      result.current.handlePaneClick();
    });

    expect(result.current.selectedNode).toBeNull();
    expect(result.current.selectedEdge).toBeNull();
    expect(result.current.isPropertiesPanelOpen).toBe(false);
  });

  it('handleNodeClick clears previously selected edge', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const edge = makeEdge('e1', 'n1', 'n2');
    act(() => { result.current.handleEdgeClick({} as React.MouseEvent, edge); });
    expect(result.current.selectedEdge).toEqual(edge);

    const node = makeNode('n1');
    act(() => { result.current.handleNodeClick({} as React.MouseEvent, node); });
    expect(result.current.selectedEdge).toBeNull();
    expect(result.current.selectedNode).toEqual(node);
  });

  it('handleEdgeClick clears previously selected node', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const node = makeNode('n1');
    act(() => { result.current.handleNodeClick({} as React.MouseEvent, node); });
    expect(result.current.selectedNode).toEqual(node);

    const edge = makeEdge('e1', 'n1', 'n2');
    act(() => { result.current.handleEdgeClick({} as React.MouseEvent, edge); });
    expect(result.current.selectedNode).toBeNull();
    expect(result.current.selectedEdge).toEqual(edge);
  });
});

// ===========================================================================
// Suite: onDragOver / onDragLeave
// ===========================================================================

describe('useCanvasInteractions — onDragOver / onDragLeave', () => {
  it('onDragOver calls preventDefault', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent();
    act(() => { result.current.onDragOver(event); });

    expect(event.preventDefault).toHaveBeenCalled();
  });

  it('onDragOver sets dropEffect to move', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent();
    act(() => { result.current.onDragOver(event); });

    expect(event.dataTransfer.dropEffect).toBe('move');
  });

  it('onDragOver sets isDragOver to true', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent();
    act(() => { result.current.onDragOver(event); });

    expect(result.current.isDragOver).toBe(true);
  });

  it('onDragLeave sets isDragOver to false', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent();
    act(() => { result.current.onDragOver(event); });
    expect(result.current.isDragOver).toBe(true);

    act(() => { result.current.onDragLeave(); });
    expect(result.current.isDragOver).toBe(false);
  });
});

// ===========================================================================
// Suite: onDrop — tool drop
// ===========================================================================

describe('useCanvasInteractions — onDrop tool', () => {
  it('calls preventDefault on drop', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ toolData: JSON.stringify(sampleTool) });
    act(() => { result.current.onDrop(event); });

    expect(event.preventDefault).toHaveBeenCalled();
  });

  it('clears isDragOver on drop', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const dragOverEvent = makeDragEvent();
    act(() => { result.current.onDragOver(dragOverEvent); });
    expect(result.current.isDragOver).toBe(true);

    const dropEvent = makeDragEvent({ toolData: JSON.stringify(sampleTool) });
    act(() => { result.current.onDrop(dropEvent); });
    expect(result.current.isDragOver).toBe(false);
  });

  it('calls screenToFlowPosition with event client coords', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ clientX: 300, clientY: 400, toolData: JSON.stringify(sampleTool) });
    act(() => { result.current.onDrop(event); });

    expect(mockScreenToFlowPosition).toHaveBeenCalledWith({ x: 300, y: 400 });
  });

  it('calls setNodes to add a step node on tool drop', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ toolData: JSON.stringify(sampleTool) });
    act(() => { result.current.onDrop(event); });

    expect(args.setNodes).toHaveBeenCalled();
    const updater = args.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[];
    const newNodes = updater([]);
    expect(newNodes).toHaveLength(1);
    expect(newNodes[0].type).toBe('stepNode');
    expect(newNodes[0].data.toolId).toBe('ai.prompt');
    expect(newNodes[0].data.toolCategory).toBe('ai');
    expect(newNodes[0].data.name).toBe('AI Prompt');
  });

  it('positions the new step node using screenToFlowPosition result', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    mockScreenToFlowPosition.mockReturnValue({ x: 50, y: 75 });
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ toolData: JSON.stringify(sampleTool) });
    act(() => { result.current.onDrop(event); });

    const updater = args.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[];
    const newNodes = updater([]);
    expect(newNodes[0].position).toEqual({ x: 50, y: 75 });
  });

  it('calls pushUndoState and markDirty on tool drop', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ toolData: JSON.stringify(sampleTool) });
    act(() => { result.current.onDrop(event); });

    expect(args.pushUndoState).toHaveBeenCalled();
    expect(args.markDirty).toHaveBeenCalled();
  });

  it('selects the new step node and opens properties panel', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ toolData: JSON.stringify(sampleTool) });
    act(() => { result.current.onDrop(event); });

    expect(result.current.selectedNode).not.toBeNull();
    expect(result.current.selectedNode?.type).toBe('stepNode');
    expect(result.current.isPropertiesPanelOpen).toBe(true);
  });

  it('logs error and does not call setNodes when tool data is invalid JSON', async () => {
    const { useCanvasInteractions } = await importHook();
    const { logger } = await import('../../../../utils/logger');
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ toolData: 'not-valid-json' });
    act(() => { result.current.onDrop(event); });

    expect(logger.error).toHaveBeenCalled();
    // setNodes should not have been called (invalid parse short-circuits before addStepNode)
    expect(args.setNodes).not.toHaveBeenCalled();
  });
});

// ===========================================================================
// Suite: onDrop — workflow-input drop
// ===========================================================================

describe('useCanvasInteractions — onDrop workflow-input', () => {
  it('creates unifiedEntryNode when no entry node exists', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs([]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ inputData: 'trigger' });
    act(() => { result.current.onDrop(event); });

    expect(args.setNodes).toHaveBeenCalled();
    const updater = args.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[];
    const newNodes = updater([]);
    expect(newNodes).toHaveLength(1);
    expect(newNodes[0].type).toBe('unifiedEntryNode');
    expect(newNodes[0].data.label).toBe('Start');
  });

  it('calls pushUndoState and markDirty when creating entry node', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs([]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ inputData: 'trigger' });
    act(() => { result.current.onDrop(event); });

    expect(args.pushUndoState).toHaveBeenCalled();
    expect(args.markDirty).toHaveBeenCalled();
  });

  it('selects new entry node and opens panel', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs([]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ inputData: 'trigger' });
    act(() => { result.current.onDrop(event); });

    expect(result.current.selectedNode).not.toBeNull();
    expect(result.current.selectedNode?.type).toBe('unifiedEntryNode');
    expect(result.current.isPropertiesPanelOpen).toBe(true);
  });

  it('selects existing entry node instead of creating a new one', async () => {
    const { useCanvasInteractions } = await importHook();
    const existingEntry = makeNode('entry-1', 'unifiedEntryNode');
    const args = makeHookArgs([existingEntry]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ inputData: 'trigger' });
    act(() => { result.current.onDrop(event); });

    // setNodes should NOT be called when entry already exists
    expect(args.setNodes).not.toHaveBeenCalled();
    expect(result.current.selectedNode).toEqual(existingEntry);
    expect(result.current.isPropertiesPanelOpen).toBe(true);
  });
});

// ===========================================================================
// Suite: onDrop — event-trigger drop
// ===========================================================================

describe('useCanvasInteractions — onDrop trigger', () => {
  it('creates eventTriggerNode on trigger drop', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs([]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ triggerData: JSON.stringify(sampleTrigger) });
    act(() => { result.current.onDrop(event); });

    expect(args.setNodes).toHaveBeenCalled();
    const updater = args.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[];
    const newNodes = updater([]);
    expect(newNodes).toHaveLength(1);
    expect(newNodes[0].type).toBe('eventTriggerNode');
    expect(newNodes[0].data.eventSource).toBe('document.created');
    expect(newNodes[0].data.name).toBe('Doc Trigger');
  });

  it('uses fallback name when trigger has no name', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs([]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const noNameTrigger = { eventSource: 'document.updated' };
    const event = makeDragEvent({ triggerData: JSON.stringify(noNameTrigger) });
    act(() => { result.current.onDrop(event); });

    const updater = args.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[];
    const newNodes = updater([]);
    expect(newNodes[0].data.name).toBe('document.updated Trigger');
  });

  it('calls pushUndoState and markDirty on trigger drop', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs([]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ triggerData: JSON.stringify(sampleTrigger) });
    act(() => { result.current.onDrop(event); });

    expect(args.pushUndoState).toHaveBeenCalled();
    expect(args.markDirty).toHaveBeenCalled();
  });

  it('selects new trigger node and opens panel', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs([]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ triggerData: JSON.stringify(sampleTrigger) });
    act(() => { result.current.onDrop(event); });

    expect(result.current.selectedNode).not.toBeNull();
    expect(result.current.selectedNode?.type).toBe('eventTriggerNode');
    expect(result.current.isPropertiesPanelOpen).toBe(true);
  });

  it('logs error and does not call setNodes when trigger data is invalid JSON', async () => {
    const { useCanvasInteractions } = await importHook();
    const { logger } = await import('../../../../utils/logger');
    const args = makeHookArgs([]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({ triggerData: '{{invalid}}' });
    act(() => { result.current.onDrop(event); });

    expect(logger.error).toHaveBeenCalled();
    expect(args.setNodes).not.toHaveBeenCalled();
  });
});

// ===========================================================================
// Suite: onDrop — no payload
// ===========================================================================

describe('useCanvasInteractions — onDrop no payload', () => {
  it('does nothing when drop has no recognized data', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs([]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const event = makeDragEvent({});
    act(() => { result.current.onDrop(event); });

    expect(args.setNodes).not.toHaveBeenCalled();
    expect(args.pushUndoState).not.toHaveBeenCalled();
  });
});

// ===========================================================================
// Suite: onConnect
// ===========================================================================

describe('useCanvasInteractions — onConnect', () => {
  it('calls setEdges with addEdge result', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const connection: Connection = { source: 'n1', target: 'n2', sourceHandle: null, targetHandle: null };
    act(() => { result.current.onConnect(connection); });

    expect(args.setEdges).toHaveBeenCalled();
  });

  it('calls setEdges updater that produces a workflowEdge-typed edge', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const connection: Connection = { source: 'n1', target: 'n2', sourceHandle: null, targetHandle: null };
    act(() => { result.current.onConnect(connection); });

    expect(args.setEdges).toHaveBeenCalled();
    // The updater passed to setEdges should add an edge with workflowEdge type
    const updater = args.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[];
    const newEdges = updater([]);
    expect(newEdges).toHaveLength(1);
    expect(newEdges[0].type).toBe('workflowEdge');
    expect(newEdges[0].animated).toBe(false);
    expect(newEdges[0].source).toBe('n1');
    expect(newEdges[0].target).toBe('n2');
  });

  it('calls pushUndoState and markDirty on connect', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const connection: Connection = { source: 'n1', target: 'n2', sourceHandle: null, targetHandle: null };
    act(() => { result.current.onConnect(connection); });

    expect(args.pushUndoState).toHaveBeenCalled();
    expect(args.markDirty).toHaveBeenCalled();
  });
});

// ===========================================================================
// Suite: handleNodesChange
// ===========================================================================

describe('useCanvasInteractions — handleNodesChange', () => {
  it('delegates to onNodesChange', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const changes: NodeChange[] = [{ type: 'select', id: 'n1', selected: true }];
    act(() => { result.current.handleNodesChange(changes); });

    expect(args.onNodesChange).toHaveBeenCalledWith(changes);
  });

  it('does NOT call markDirty for select-only changes', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const changes: NodeChange[] = [{ type: 'select', id: 'n1', selected: true }];
    act(() => { result.current.handleNodesChange(changes); });

    expect(args.markDirty).not.toHaveBeenCalled();
  });

  it('does NOT call markDirty for dimensions-only changes', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const changes: NodeChange[] = [
      { type: 'dimensions', id: 'n1', dimensions: { width: 100, height: 50 }, resizing: false },
    ];
    act(() => { result.current.handleNodesChange(changes); });

    expect(args.markDirty).not.toHaveBeenCalled();
  });

  it('calls markDirty for position changes', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const changes: NodeChange[] = [
      { type: 'position', id: 'n1', position: { x: 10, y: 20 } },
    ];
    act(() => { result.current.handleNodesChange(changes); });

    expect(args.markDirty).toHaveBeenCalled();
  });

  it('calls markDirty for remove changes', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const changes: NodeChange[] = [{ type: 'remove', id: 'n1' }];
    act(() => { result.current.handleNodesChange(changes); });

    expect(args.markDirty).toHaveBeenCalled();
  });
});

// ===========================================================================
// Suite: handleEdgesChange
// ===========================================================================

describe('useCanvasInteractions — handleEdgesChange', () => {
  it('delegates to onEdgesChange', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const changes: EdgeChange[] = [{ type: 'select', id: 'e1', selected: true }];
    act(() => { result.current.handleEdgesChange(changes); });

    expect(args.onEdgesChange).toHaveBeenCalledWith(changes);
  });

  it('does NOT call markDirty for select-only edge changes', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const changes: EdgeChange[] = [{ type: 'select', id: 'e1', selected: true }];
    act(() => { result.current.handleEdgesChange(changes); });

    expect(args.markDirty).not.toHaveBeenCalled();
  });

  it('calls markDirty for remove edge changes', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const changes: EdgeChange[] = [{ type: 'remove', id: 'e1' }];
    act(() => { result.current.handleEdgesChange(changes); });

    expect(args.markDirty).toHaveBeenCalled();
  });
});

// ===========================================================================
// Suite: handleNodeUpdate
// ===========================================================================

describe('useCanvasInteractions — handleNodeUpdate', () => {
  it('calls setNodes updater that patches the target node data', async () => {
    const { useCanvasInteractions } = await importHook();
    const existingNode: Node = {
      id: 'n1',
      type: 'stepNode',
      position: { x: 0, y: 0 },
      data: { name: 'Old Name', toolType: 'system_tool', toolId: 'x', toolName: 'X', toolCategory: 'ai', configuration: {}, continueOnError: false },
    };
    const args = makeHookArgs([existingNode]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    act(() => { result.current.handleNodeUpdate('n1', { name: 'New Name' }); });

    expect(args.setNodes).toHaveBeenCalled();
    const updater = args.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[];
    const updated = updater([existingNode]);
    expect(updated[0].data.name).toBe('New Name');
  });

  it('leaves non-target nodes unchanged', async () => {
    const { useCanvasInteractions } = await importHook();
    const n1: Node = { id: 'n1', type: 'stepNode', position: { x: 0, y: 0 }, data: { name: 'A' } };
    const n2: Node = { id: 'n2', type: 'stepNode', position: { x: 0, y: 0 }, data: { name: 'B' } };
    const args = makeHookArgs([n1, n2]);
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    act(() => { result.current.handleNodeUpdate('n1', { name: 'Updated' }); });

    const updater = args.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[];
    const updated = updater([n1, n2]);
    expect(updated[1].data.name).toBe('B');
  });

  it('calls pushUndoState and markDirty on update', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    act(() => { result.current.handleNodeUpdate('n1', { name: 'X' }); });

    expect(args.pushUndoState).toHaveBeenCalled();
    expect(args.markDirty).toHaveBeenCalled();
  });
});

// ===========================================================================
// Suite: deleteSelectedNode
// ===========================================================================

describe('useCanvasInteractions — deleteSelectedNode', () => {
  it('does nothing when no node is selected', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    act(() => { result.current.deleteSelectedNode(); });

    expect(args.setNodes).not.toHaveBeenCalled();
    expect(args.pushUndoState).not.toHaveBeenCalled();
  });

  it('removes the selected node from the node list', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const node = makeNode('n1');
    act(() => { result.current.handleNodeClick({} as React.MouseEvent, node); });
    act(() => { result.current.deleteSelectedNode(); });

    expect(args.setNodes).toHaveBeenCalled();
    const updater = args.setNodes.mock.calls[0][0] as (nds: Node[]) => Node[];
    const remaining = updater([node, makeNode('n2')]);
    expect(remaining.map((n) => n.id)).not.toContain('n1');
    expect(remaining.map((n) => n.id)).toContain('n2');
  });

  it('removes edges connected to the deleted node', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const node = makeNode('n1');
    act(() => { result.current.handleNodeClick({} as React.MouseEvent, node); });
    act(() => { result.current.deleteSelectedNode(); });

    expect(args.setEdges).toHaveBeenCalled();
    const edgeUpdater = args.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[];
    const edges = [
      makeEdge('e1', 'n1', 'n2'),
      makeEdge('e2', 'n2', 'n1'),
      makeEdge('e3', 'n2', 'n3'),
    ];
    const remaining = edgeUpdater(edges);
    expect(remaining.map((e) => e.id)).toEqual(['e3']);
  });

  it('clears selectedNode and closes panel after deletion', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const node = makeNode('n1');
    act(() => { result.current.handleNodeClick({} as React.MouseEvent, node); });
    expect(result.current.isPropertiesPanelOpen).toBe(true);

    act(() => { result.current.deleteSelectedNode(); });

    expect(result.current.selectedNode).toBeNull();
    expect(result.current.isPropertiesPanelOpen).toBe(false);
  });

  it('calls pushUndoState and markDirty when deleting a node', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const node = makeNode('n1');
    act(() => { result.current.handleNodeClick({} as React.MouseEvent, node); });
    act(() => { result.current.deleteSelectedNode(); });

    expect(args.pushUndoState).toHaveBeenCalled();
    expect(args.markDirty).toHaveBeenCalled();
  });
});

// ===========================================================================
// Suite: deleteSelectedEdge
// ===========================================================================

describe('useCanvasInteractions — deleteSelectedEdge', () => {
  it('does nothing when no edge is selected', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    act(() => { result.current.deleteSelectedEdge(); });

    expect(args.setEdges).not.toHaveBeenCalled();
    expect(args.pushUndoState).not.toHaveBeenCalled();
  });

  it('removes the selected edge', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const edge = makeEdge('e1', 'n1', 'n2');
    act(() => { result.current.handleEdgeClick({} as React.MouseEvent, edge); });
    act(() => { result.current.deleteSelectedEdge(); });

    expect(args.setEdges).toHaveBeenCalled();
    const updater = args.setEdges.mock.calls[0][0] as (eds: Edge[]) => Edge[];
    const edges = [makeEdge('e1', 'n1', 'n2'), makeEdge('e2', 'n2', 'n3')];
    const remaining = updater(edges);
    expect(remaining.map((e) => e.id)).toEqual(['e2']);
  });

  it('clears selectedEdge and closes panel after deletion', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const edge = makeEdge('e1', 'n1', 'n2');
    act(() => { result.current.handleEdgeClick({} as React.MouseEvent, edge); });
    expect(result.current.isPropertiesPanelOpen).toBe(true);

    act(() => { result.current.deleteSelectedEdge(); });

    expect(result.current.selectedEdge).toBeNull();
    expect(result.current.isPropertiesPanelOpen).toBe(false);
  });

  it('calls pushUndoState and markDirty when deleting an edge', async () => {
    const { useCanvasInteractions } = await importHook();
    const args = makeHookArgs();
    const { result } = renderHook(() =>
      useCanvasInteractions(
        args.nodes, args.setNodes, args.setEdges,
        args.onNodesChange, args.onEdgesChange,
        args.pushUndoState, args.markDirty,
      ),
    );

    const edge = makeEdge('e1', 'n1', 'n2');
    act(() => { result.current.handleEdgeClick({} as React.MouseEvent, edge); });
    act(() => { result.current.deleteSelectedEdge(); });

    expect(args.pushUndoState).toHaveBeenCalled();
    expect(args.markDirty).toHaveBeenCalled();
  });
});
