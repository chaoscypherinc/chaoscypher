// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import Graph from 'graphology';
import type { SigmaNodeEventPayload } from 'sigma/types';
import type { NodeAttributes, EdgeAttributes, GraphNodeData } from '../../types';
import { unresolvedBucketId } from '../../types';
import { useUnresolvedBucket } from '../useUnresolvedBucket';
import { useFocusNode } from '../useFocusNode';
import { useNodeDoubleClick } from '../useNodeDoubleClick';
import { useSigmaEvents } from '../useSigmaEvents';

/** The hub for unresolved nodes with no known source. */
const UNRESOLVED_BUCKET_ID = unresolvedBucketId(null);

type Handler = (payload: unknown) => void;
const holder = vi.hoisted(() => ({ sigma: null as unknown }));
vi.mock('@react-sigma/core', () => ({ useSigma: () => holder.sigma }));

function makeFakeSigma(graph: Graph<NodeAttributes, EdgeAttributes>) {
  const handlers = new Map<string, Handler>();
  const animate = vi.fn();
  return {
    handlers,
    animate,
    getGraph: () => graph,
    getCamera: () => ({ animate }),
    getNodeDisplayData: (id: string) => ({ x: graph.getNodeAttribute(id, 'x'), y: graph.getNodeAttribute(id, 'y') }),
    on: (event: string, handler: Handler) => handlers.set(event, handler),
    off: () => {},
  };
}

function attrs(id: string, content: Record<string, unknown>, x = 0, y = 0): NodeAttributes {
  return {
    nodeId: id, title: id, content, templateId: 't', tags: [], createdAt: '', updatedAt: '',
    x, y, size: 5, color: '#00E5FF', label: id,
  };
}

/** 'hidden' is unresolved (inside the hub), 'plain' is an ordinary node. */
function makeGraph() {
  const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: 'directed' });
  graph.addNode('hidden', attrs('hidden', { orphan: true }, 0.25, 0.75));
  graph.addNode('plain', attrs('plain', {}, 0.5, 0.5));
  return graph;
}

function renderCanvas(graph: Graph<NodeAttributes, EdgeAttributes>) {
  const sigma = makeFakeSigma(graph);
  holder.sigma = sigma;
  const onSelect = vi.fn<(nodeId: string, data: GraphNodeData) => void>();
  const noop = () => {};
  const view = renderHook(() => {
    const bucket = useUnresolvedBucket(graph);
    const focusNode = useFocusNode({ graph, bucket, onSelect });
    const onNodeDoubleClick = useNodeDoubleClick({
      onToggleSourceGroup: noop,
      onToggleUnresolved: bucket.toggle,
      onOpenNode: noop,
    });
    useSigmaEvents({
      onNodeClick: noop,
      onEdgeClick: noop,
      onStageClick: noop,
      onNodeRightClick: noop,
      onEdgeRightClick: noop,
      onStageRightClick: noop,
      onNodeDoubleClick,
    });
    return { bucket, focusNode };
  });
  return { sigma, view, onSelect };
}

describe('useFocusNode', () => {
  it('focusing a node hidden in the collapsed hub opens the hub, then selects and centres it', () => {
    const { sigma, view, onSelect } = renderCanvas(makeGraph());
    expect(view.result.current.bucket.hiddenIds.has('hidden')).toBe(true);

    act(() => view.result.current.focusNode('hidden'));

    expect(view.result.current.bucket.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(true);
    expect(view.result.current.bucket.hiddenIds.has('hidden')).toBe(false);
    expect(onSelect).toHaveBeenCalledWith('hidden', expect.objectContaining({ nodeId: 'hidden' }));
    expect(sigma.animate).toHaveBeenCalledWith(
      expect.objectContaining({ x: 0.25, y: 0.75 }),
      expect.anything(),
    );
  });

  it('focusing an ordinary node leaves the hub as it is', () => {
    const { view, onSelect } = renderCanvas(makeGraph());

    act(() => view.result.current.focusNode('plain'));

    expect(view.result.current.bucket.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(false);
    expect(view.result.current.bucket.hiddenIds.has('hidden')).toBe(true);
    expect(onSelect).toHaveBeenCalledWith('plain', expect.objectContaining({ nodeId: 'plain' }));
  });

  it('after a search opened the hub, a double-click on the hub still closes and reopens it', () => {
    const { sigma, view } = renderCanvas(makeGraph());
    act(() => view.result.current.focusNode('hidden'));
    expect(view.result.current.bucket.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(true);

    const doubleClick = () =>
      act(() => {
        sigma.handlers.get('doubleClickNode')?.({
          node: UNRESOLVED_BUCKET_ID,
          event: { original: new MouseEvent('dblclick') },
        } as unknown as SigmaNodeEventPayload);
      });

    doubleClick();
    expect(view.result.current.bucket.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(false);
    expect(view.result.current.bucket.hiddenIds.has('hidden')).toBe(true);
    doubleClick();
    expect(view.result.current.bucket.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(true);
  });

  it('focusing an already shown member keeps the hub open, and an unknown id does nothing', () => {
    const { view, onSelect } = renderCanvas(makeGraph());
    act(() => view.result.current.focusNode('hidden'));
    act(() => view.result.current.focusNode('hidden'));
    expect(view.result.current.bucket.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(true);

    onSelect.mockClear();
    act(() => view.result.current.focusNode('gone'));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('opens the hub of the focused node\'s own source, and no other', () => {
    const graph = makeGraph();
    graph.addNode('s1-node', { ...attrs('s1-node', { orphan: true }), sourceDocumentId: 's1' });
    graph.addNode('s2-node', { ...attrs('s2-node', { orphan: true }), sourceDocumentId: 's2' });
    const { view } = renderCanvas(graph);

    act(() => view.result.current.focusNode('s2-node'));

    const { bucket } = view.result.current;
    expect(bucket.isExpanded(unresolvedBucketId('s2'))).toBe(true);
    expect(bucket.isExpanded(unresolvedBucketId('s1'))).toBe(false);
    expect(bucket.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(false);
    expect(bucket.hiddenIds.has('s2-node')).toBe(false);
    expect(bucket.hiddenIds.has('s1-node')).toBe(true);
  });
});
