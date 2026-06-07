// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import Graph from 'graphology';
import type {
  SigmaNodeEventPayload,
  SigmaEdgeEventPayload,
  SigmaStageEventPayload,
} from 'sigma/types';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import { useSigmaEvents } from '../useSigmaEvents';

// ---------------------------------------------------------------------------
// Fake sigma: capture the handlers registered via sigma.on(event, handler),
// and record sigma.off(...) calls so we can assert cleanup on unmount.
// ---------------------------------------------------------------------------

type EventHandler = (payload: unknown) => void;

interface FakeSigma {
  on: ReturnType<typeof vi.fn<(event: string, handler: EventHandler) => void>>;
  off: ReturnType<typeof vi.fn<(event: string, handler: EventHandler) => void>>;
  getGraph: () => Graph<NodeAttributes, EdgeAttributes>;
  /** Latest handler captured per event name (last `on` wins). */
  handlers: Map<string, EventHandler>;
}

// vi.hoisted so the (hoisted) mock factory can close over a mutable holder
// that each test reassigns before rendering the hook.
const holder = vi.hoisted(() => ({ sigma: null as unknown }));

vi.mock('@react-sigma/core', () => ({
  useSigma: () => holder.sigma,
}));

function makeFakeSigma(graph: Graph<NodeAttributes, EdgeAttributes>): FakeSigma {
  const handlers = new Map<string, EventHandler>();
  const fake: FakeSigma = {
    handlers,
    getGraph: () => graph,
    on: vi.fn<(event: string, handler: EventHandler) => void>((event, handler) => {
      handlers.set(event, handler);
    }),
    off: vi.fn<(event: string, handler: EventHandler) => void>(),
  };
  return fake;
}

// ---------------------------------------------------------------------------
// Attribute + graph builders.
// ---------------------------------------------------------------------------

function nodeAttrs(overrides: Partial<NodeAttributes> = {}): NodeAttributes {
  return {
    nodeId: 'n1',
    title: 'Node One',
    content: { body: 'hello' },
    templateId: 't1',
    type: 'entity',
    tags: ['a', 'b'],
    createdAt: '2026-01-01',
    updatedAt: '2026-01-02',
    sourceDocumentId: 'doc1',
    sourceDocumentName: 'Doc One',
    x: 1,
    y: 2,
    size: 5,
    color: '#00E5FF',
    label: 'Node One',
    ...overrides,
  };
}

function edgeAttrs(overrides: Partial<EdgeAttributes> = {}): EdgeAttributes {
  return {
    edgeId: 'e1',
    label: 'relates_to',
    templateId: 't1',
    sourceId: 'n1',
    targetId: 'n2',
    type: 'arrow',
    properties: { weight: 3 },
    createdAt: '2026-01-01',
    updatedAt: '2026-01-02',
    ...overrides,
  };
}

function buildGraph(): Graph<NodeAttributes, EdgeAttributes> {
  const graph = new Graph<NodeAttributes, EdgeAttributes>();
  graph.addNode('n1', nodeAttrs({ nodeId: 'n1' }));
  graph.addNode('n2', nodeAttrs({ nodeId: 'n2', title: 'Node Two', label: 'Node Two' }));
  graph.addEdgeWithKey('e1', 'n1', 'n2', edgeAttrs({ edgeId: 'e1' }));
  return graph;
}

// ---------------------------------------------------------------------------
// Synthetic payload builders.
// ---------------------------------------------------------------------------

function nodePayload(node: string, original?: unknown): SigmaNodeEventPayload {
  return {
    node,
    event: { original: original ?? new MouseEvent('contextmenu') },
  } as unknown as SigmaNodeEventPayload;
}

function edgePayload(edge: string, original?: unknown): SigmaEdgeEventPayload {
  return {
    edge,
    event: { original: original ?? new MouseEvent('contextmenu') },
  } as unknown as SigmaEdgeEventPayload;
}

function stagePayload(original?: unknown): SigmaStageEventPayload {
  return {
    event: { original: original ?? new MouseEvent('contextmenu') },
  } as unknown as SigmaStageEventPayload;
}

// ---------------------------------------------------------------------------
// Render helper.
// ---------------------------------------------------------------------------

interface Callbacks {
  onNodeClick: ReturnType<typeof vi.fn>;
  onEdgeClick: ReturnType<typeof vi.fn>;
  onStageClick: ReturnType<typeof vi.fn>;
  onNodeRightClick: ReturnType<typeof vi.fn>;
  onEdgeRightClick: ReturnType<typeof vi.fn>;
  onStageRightClick: ReturnType<typeof vi.fn>;
  onNodeDoubleClick: ReturnType<typeof vi.fn>;
}

function makeCallbacks(): Callbacks {
  return {
    onNodeClick: vi.fn(),
    onEdgeClick: vi.fn(),
    onStageClick: vi.fn(),
    onNodeRightClick: vi.fn(),
    onEdgeRightClick: vi.fn(),
    onStageRightClick: vi.fn(),
    onNodeDoubleClick: vi.fn(),
  };
}

function renderEvents(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  callbacks: Callbacks = makeCallbacks(),
) {
  const sigma = makeFakeSigma(graph);
  holder.sigma = sigma;
  const view = renderHook(
    (cb: Callbacks) => useSigmaEvents(cb as unknown as Parameters<typeof useSigmaEvents>[0]),
    { initialProps: callbacks },
  );
  return { sigma, view, callbacks };
}

// ---------------------------------------------------------------------------

const BOUND_EVENTS = [
  'clickNode',
  'clickEdge',
  'clickStage',
  'rightClickNode',
  'rightClickEdge',
  'rightClickStage',
  'doubleClickNode',
];

describe('useSigmaEvents', () => {
  beforeEach(() => {
    holder.sigma = null;
  });

  it('binds all sigma event handlers on mount', () => {
    const { sigma } = renderEvents(buildGraph());
    const boundEvents = sigma.on.mock.calls.map(([event]) => event);
    for (const event of BOUND_EVENTS) {
      expect(boundEvents).toContain(event);
    }
    expect(sigma.on).toHaveBeenCalledTimes(BOUND_EVENTS.length);
  });

  describe('clickNode', () => {
    it('invokes onNodeClick with the node id and extracted node data', () => {
      const { sigma, callbacks } = renderEvents(buildGraph());
      const handler = sigma.handlers.get('clickNode');
      expect(handler).toBeDefined();
      handler?.(nodePayload('n1'));

      expect(callbacks.onNodeClick).toHaveBeenCalledTimes(1);
      const [nodeId, data] = callbacks.onNodeClick.mock.calls[0];
      expect(nodeId).toBe('n1');
      expect(data).toMatchObject({
        nodeId: 'n1',
        title: 'Node One',
        templateId: 't1',
        type: 'entity',
        tags: ['a', 'b'],
        sourceDocumentId: 'doc1',
        sourceDocumentName: 'Doc One',
      });
      // extractNodeData should NOT carry rendering attrs.
      expect(data).not.toHaveProperty('x');
      expect(data).not.toHaveProperty('color');
    });
  });

  describe('clickEdge', () => {
    it('invokes onEdgeClick with the edge id and extracted edge data', () => {
      const { sigma, callbacks } = renderEvents(buildGraph());
      const handler = sigma.handlers.get('clickEdge');
      handler?.(edgePayload('e1'));

      expect(callbacks.onEdgeClick).toHaveBeenCalledTimes(1);
      const [edgeId, data] = callbacks.onEdgeClick.mock.calls[0];
      expect(edgeId).toBe('e1');
      expect(data).toMatchObject({
        edgeId: 'e1',
        label: 'relates_to',
        sourceId: 'n1',
        targetId: 'n2',
        type: 'arrow',
        properties: { weight: 3 },
      });
    });
  });

  describe('clickStage', () => {
    it('invokes onStageClick with no arguments', () => {
      const { sigma, callbacks } = renderEvents(buildGraph());
      const handler = sigma.handlers.get('clickStage');
      handler?.(stagePayload());

      expect(callbacks.onStageClick).toHaveBeenCalledTimes(1);
      expect(callbacks.onStageClick).toHaveBeenCalledWith();
    });
  });

  describe('rightClickNode', () => {
    it('prevents default and invokes onNodeRightClick with the MouseEvent', () => {
      const { sigma, callbacks } = renderEvents(buildGraph());
      const original = new MouseEvent('contextmenu');
      const preventDefault = vi.spyOn(original, 'preventDefault');
      const handler = sigma.handlers.get('rightClickNode');
      handler?.(nodePayload('n1', original));

      expect(preventDefault).toHaveBeenCalledTimes(1);
      expect(callbacks.onNodeRightClick).toHaveBeenCalledTimes(1);
      const [nodeId, data, event] = callbacks.onNodeRightClick.mock.calls[0];
      expect(nodeId).toBe('n1');
      expect(data).toMatchObject({ nodeId: 'n1', title: 'Node One' });
      expect(event).toBe(original);
    });

    it('skips the callback when the original event is not a MouseEvent', () => {
      const { sigma, callbacks } = renderEvents(buildGraph());
      // A bare object with preventDefault but not a MouseEvent instance.
      const original = { preventDefault: vi.fn() };
      const handler = sigma.handlers.get('rightClickNode');
      handler?.(nodePayload('n1', original));

      expect(original.preventDefault).toHaveBeenCalledTimes(1);
      expect(callbacks.onNodeRightClick).not.toHaveBeenCalled();
    });
  });

  describe('rightClickEdge', () => {
    it('prevents default and invokes onEdgeRightClick with the MouseEvent', () => {
      const { sigma, callbacks } = renderEvents(buildGraph());
      const original = new MouseEvent('contextmenu');
      const preventDefault = vi.spyOn(original, 'preventDefault');
      const handler = sigma.handlers.get('rightClickEdge');
      handler?.(edgePayload('e1', original));

      expect(preventDefault).toHaveBeenCalledTimes(1);
      expect(callbacks.onEdgeRightClick).toHaveBeenCalledTimes(1);
      const [edgeId, data, event] = callbacks.onEdgeRightClick.mock.calls[0];
      expect(edgeId).toBe('e1');
      expect(data).toMatchObject({ edgeId: 'e1', label: 'relates_to' });
      expect(event).toBe(original);
    });

    it('skips the callback when the original event is not a MouseEvent', () => {
      const { sigma, callbacks } = renderEvents(buildGraph());
      const original = { preventDefault: vi.fn() };
      const handler = sigma.handlers.get('rightClickEdge');
      handler?.(edgePayload('e1', original));

      expect(original.preventDefault).toHaveBeenCalledTimes(1);
      expect(callbacks.onEdgeRightClick).not.toHaveBeenCalled();
    });
  });

  describe('rightClickStage', () => {
    it('prevents default and invokes onStageRightClick with the MouseEvent', () => {
      const { sigma, callbacks } = renderEvents(buildGraph());
      const original = new MouseEvent('contextmenu');
      const preventDefault = vi.spyOn(original, 'preventDefault');
      const handler = sigma.handlers.get('rightClickStage');
      handler?.(stagePayload(original));

      expect(preventDefault).toHaveBeenCalledTimes(1);
      expect(callbacks.onStageRightClick).toHaveBeenCalledTimes(1);
      expect(callbacks.onStageRightClick).toHaveBeenCalledWith(original);
    });

    it('skips the callback when the original event is not a MouseEvent', () => {
      const { sigma, callbacks } = renderEvents(buildGraph());
      const original = { preventDefault: vi.fn() };
      const handler = sigma.handlers.get('rightClickStage');
      handler?.(stagePayload(original));

      expect(original.preventDefault).toHaveBeenCalledTimes(1);
      expect(callbacks.onStageRightClick).not.toHaveBeenCalled();
    });
  });

  describe('doubleClickNode', () => {
    it('prevents default and invokes onNodeDoubleClick with extracted data', () => {
      const { sigma, callbacks } = renderEvents(buildGraph());
      const original = new MouseEvent('dblclick');
      const preventDefault = vi.spyOn(original, 'preventDefault');
      const handler = sigma.handlers.get('doubleClickNode');
      handler?.(nodePayload('n2', original));

      expect(preventDefault).toHaveBeenCalledTimes(1);
      expect(callbacks.onNodeDoubleClick).toHaveBeenCalledTimes(1);
      const [nodeId, data] = callbacks.onNodeDoubleClick.mock.calls[0];
      expect(nodeId).toBe('n2');
      expect(data).toMatchObject({ nodeId: 'n2', title: 'Node Two' });
    });
  });

  describe('cleanup', () => {
    it('unbinds every event with off() on unmount', () => {
      const { sigma, view } = renderEvents(buildGraph());
      expect(sigma.off).not.toHaveBeenCalled();

      view.unmount();

      const offEvents = sigma.off.mock.calls.map(([event]) => event);
      for (const event of BOUND_EVENTS) {
        expect(offEvents).toContain(event);
      }
      expect(sigma.off).toHaveBeenCalledTimes(BOUND_EVENTS.length);
    });

    it('unbinds the same handler references that were bound', () => {
      const { sigma, view } = renderEvents(buildGraph());
      const onPairs = new Map(sigma.on.mock.calls.map(([e, h]) => [e, h]));

      view.unmount();

      for (const [event, handler] of sigma.off.mock.calls) {
        expect(onPairs.get(event)).toBe(handler);
      }
    });
  });

  describe('re-render', () => {
    it('rebinds handlers wired to the new callback props when a callback changes', () => {
      const first = makeCallbacks();
      const { sigma, view } = renderEvents(buildGraph(), first);

      const onCallsAfterFirst = sigma.on.mock.calls.length;
      const offCallsAfterFirst = sigma.off.mock.calls.length;

      const second = makeCallbacks();
      view.rerender(second);

      // Effect re-ran: previous handlers were unbound and new ones bound.
      expect(sigma.off.mock.calls.length).toBeGreaterThan(offCallsAfterFirst);
      expect(sigma.on.mock.calls.length).toBeGreaterThan(onCallsAfterFirst);

      // The freshly-captured clickNode handler now calls the NEW callback.
      const handler = sigma.handlers.get('clickNode');
      handler?.(nodePayload('n1'));
      expect(second.onNodeClick).toHaveBeenCalledTimes(1);
      expect(first.onNodeClick).not.toHaveBeenCalled();
    });
  });
});
