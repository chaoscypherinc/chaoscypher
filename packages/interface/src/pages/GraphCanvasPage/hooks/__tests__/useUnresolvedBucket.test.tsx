// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import Graph from 'graphology';
import type { SigmaNodeEventPayload } from 'sigma/types';
import type { NodeAttributes, EdgeAttributes, GraphNodeData } from '../../types';
import { SOURCE_GROUP_PREFIX, unresolvedBucketId } from '../../types';
import { useUnresolvedBucket } from '../useUnresolvedBucket';
import { unresolvedRootEdgeId } from '../../utils/unresolvedBucket';
import { addSourceGroupNode } from '../../utils/transformers';
import { useNodeDoubleClick } from '../useNodeDoubleClick';
import { useSigmaEvents } from '../useSigmaEvents';

/** The hub for unresolved nodes with no known source. */
const UNRESOLVED_BUCKET_ID = unresolvedBucketId(null);

// Fake sigma for useSigmaEvents: records the handler bound per event.
type Handler = (payload: unknown) => void;
const holder = vi.hoisted(() => ({ sigma: null as unknown }));
vi.mock('@react-sigma/core', () => ({ useSigma: () => holder.sigma }));

function makeFakeSigma(graph: Graph<NodeAttributes, EdgeAttributes>) {
  const handlers = new Map<string, Handler>();
  return {
    handlers,
    getGraph: () => graph,
    on: (event: string, handler: Handler) => handlers.set(event, handler),
    off: () => {},
  };
}

function attrs(id: string, content: Record<string, unknown>): NodeAttributes {
  return {
    nodeId: id, title: id, content, templateId: 't', tags: [], createdAt: '', updatedAt: '',
    x: 0, y: 0, size: 5, color: '#00E5FF', label: id,
  };
}

function makeGraph() {
  const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: 'directed' });
  graph.addNode('a', attrs('a', { orphan: true }));
  graph.addNode('b', attrs('b', {}));
  graph.addEdgeWithKey('ab', 'a', 'b', {
    edgeId: 'ab', label: '', templateId: 't', sourceId: 'a', targetId: 'b',
    properties: {}, createdAt: '', updatedAt: '',
  });
  return graph;
}

function doubleClick(sigma: ReturnType<typeof makeFakeSigma>, node: string) {
  act(() => {
    sigma.handlers.get('doubleClickNode')?.({
      node,
      event: { original: new MouseEvent('dblclick') },
    } as unknown as SigmaNodeEventPayload);
  });
}

describe('useUnresolvedBucket', () => {
  it('adds the hub for the unresolved nodes and starts collapsed, members hidden', () => {
    const graph = makeGraph();
    const { result } = renderHook(() => useUnresolvedBucket(graph));

    expect(graph.hasNode(UNRESOLVED_BUCKET_ID)).toBe(true);
    expect(graph.getNodeAttribute(UNRESOLVED_BUCKET_ID, 'label')).toBe('Unresolved (1)');
    expect(result.current.memberIds).toEqual(['a']);
    expect(result.current.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(false);
    expect(result.current.hiddenIds).toEqual(new Set(['a']));
  });

  it('toggles between collapsed and expanded without touching the graph', () => {
    const graph = makeGraph();
    const { result } = renderHook(() => useUnresolvedBucket(graph));
    const before = { order: graph.order, size: graph.size };

    act(() => result.current.toggle(UNRESOLVED_BUCKET_ID));
    expect(result.current.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(true);
    expect(result.current.hiddenIds.size).toBe(0);

    act(() => result.current.toggle(UNRESOLVED_BUCKET_ID));
    expect(result.current.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(false);
    expect(result.current.hiddenIds).toEqual(new Set(['a']));
    expect({ order: graph.order, size: graph.size }).toEqual(before);
  });

  it('follows the graph as nodes are added, re-flagged, dropped or cleared', () => {
    const graph = makeGraph();
    const { result } = renderHook(() => useUnresolvedBucket(graph));

    act(() => {
      graph.addNode('c', attrs('c', { descriptor_reference: true }));
    });
    expect(result.current.memberIds).toEqual(['a', 'c']);
    expect(graph.getNodeAttribute(UNRESOLVED_BUCKET_ID, 'label')).toBe('Unresolved (2)');

    act(() => {
      graph.setNodeAttribute('b', 'content', { low_plausibility: true });
    });
    expect(result.current.memberIds).toEqual(['a', 'b', 'c']);

    act(() => {
      graph.dropNode('a');
    });
    expect(result.current.memberIds).toEqual(['b', 'c']);

    act(() => {
      graph.clear();
    });
    expect(result.current.memberIds).toEqual([]);
    expect(graph.hasNode(UNRESOLVED_BUCKET_ID)).toBe(false);

    // A reload brings the hub back.
    act(() => {
      graph.addNode('d', attrs('d', { orphan: true }));
    });
    expect(graph.hasNode(UNRESOLVED_BUCKET_ID)).toBe(true);
  });

  it('shows no hub when nothing is unresolved', () => {
    const graph = new Graph<NodeAttributes, EdgeAttributes>();
    graph.addNode('b', attrs('b', {}));
    const { result } = renderHook(() => useUnresolvedBucket(graph));
    expect(graph.hasNode(UNRESOLVED_BUCKET_ID)).toBe(false);
    expect(result.current.memberIds).toEqual([]);
    expect(result.current.hiddenIds.size).toBe(0);
  });
});

describe('one hub per source', () => {
  function makeSourcedGraph() {
    const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: 'directed' });
    graph.addNode('u1', { ...attrs('u1', { orphan: true }), sourceDocumentId: 's1' });
    graph.addNode('u2', { ...attrs('u2', { orphan: true }), sourceDocumentId: 's1' });
    graph.addNode('u3', { ...attrs('u3', { orphan: true }), sourceDocumentId: 's2' });
    return graph;
  }
  const S1 = unresolvedBucketId('s1');
  const S2 = unresolvedBucketId('s2');

  it('keeps one hub per source document, with its own members', () => {
    const graph = makeSourcedGraph();
    const { result } = renderHook(() => useUnresolvedBucket(graph));
    expect(result.current.hubs).toEqual(new Map([[S1, ['u1', 'u2']], [S2, ['u3']]]));
    expect(result.current.hubOf.get('u3')).toBe(S2);
    expect(graph.getNodeAttribute(S1, 'label')).toBe('Unresolved (2)');
  });

  it('opens and closes each hub on its own', () => {
    const graph = makeSourcedGraph();
    const { result } = renderHook(() => useUnresolvedBucket(graph));
    expect(result.current.hiddenIds).toEqual(new Set(['u1', 'u2', 'u3']));

    act(() => result.current.toggle(S1));
    expect(result.current.isExpanded(S1)).toBe(true);
    expect(result.current.isExpanded(S2)).toBe(false);
    expect(result.current.hiddenIds).toEqual(new Set(['u3']));

    act(() => result.current.toggle(S2));
    act(() => result.current.toggle(S1));
    expect(result.current.hiddenIds).toEqual(new Set(['u1', 'u2']));
  });

  it('links a hub to its document root once the root loads', () => {
    const graph = makeSourcedGraph();
    renderHook(() => useUnresolvedBucket(graph));
    expect(graph.hasEdge(unresolvedRootEdgeId('s1'))).toBe(false);

    act(() => {
      addSourceGroupNode(graph, {
        source_id: 's1', title: 'Doc', source_type: 'text', filename: 'doc.txt',
        entity_count: 2, entity_node_ids: ['u1', 'u2'],
      });
    });
    expect(graph.hasEdge(unresolvedRootEdgeId('s1'))).toBe(true);
  });
});

describe('double-clicking on the canvas', () => {
  function renderCanvas(graph: Graph<NodeAttributes, EdgeAttributes>) {
    const sigma = makeFakeSigma(graph);
    holder.sigma = sigma;
    const onToggleSourceGroup = vi.fn<(sourceId: string) => void>();
    const onOpenNode = vi.fn<(data: GraphNodeData) => void>();
    const noop = () => {};
    const view = renderHook(() => {
      const bucket = useUnresolvedBucket(graph);
      const onNodeDoubleClick = useNodeDoubleClick({
        onToggleSourceGroup,
        onToggleUnresolved: bucket.toggle,
        onOpenNode,
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
      return bucket;
    });
    return { sigma, view, onToggleSourceGroup, onOpenNode };
  }

  it('double-clicking a hub shows only its members, and again hides them', () => {
    const graph = makeGraph();
    graph.addNode('s', { ...attrs('s', { orphan: true }), sourceDocumentId: 'src' });
    const { sigma, view } = renderCanvas(graph);
    doubleClick(sigma, unresolvedBucketId('src'));
    expect(view.result.current.isExpanded(unresolvedBucketId('src'))).toBe(true);
    expect(view.result.current.hiddenIds).toEqual(new Set(['a']));
    doubleClick(sigma, unresolvedBucketId('src'));
    expect(view.result.current.hiddenIds).toEqual(new Set(['a', 's']));
  });

  it('double-clicking the Unresolved hub shows its members, and again hides them', () => {
    const graph = makeGraph();
    const { sigma, view, onOpenNode } = renderCanvas(graph);
    expect(view.result.current.hiddenIds).toEqual(new Set(['a']));

    doubleClick(sigma, UNRESOLVED_BUCKET_ID);
    expect(view.result.current.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(true);
    expect(view.result.current.hiddenIds.size).toBe(0);

    doubleClick(sigma, UNRESOLVED_BUCKET_ID);
    expect(view.result.current.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(false);
    expect(view.result.current.hiddenIds).toEqual(new Set(['a']));

    // The hub never opens the node editor.
    expect(onOpenNode).not.toHaveBeenCalled();
  });

  it('double-clicking a real node still opens it, and leaves the hub alone', () => {
    const graph = makeGraph();
    const { sigma, view, onOpenNode, onToggleSourceGroup } = renderCanvas(graph);

    doubleClick(sigma, 'a');
    expect(onOpenNode).toHaveBeenCalledTimes(1);
    expect(onOpenNode.mock.calls[0][0]).toMatchObject({ nodeId: 'a' });
    expect(view.result.current.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(false);
    expect(onToggleSourceGroup).not.toHaveBeenCalled();
  });

  it('double-clicking a source root still toggles its source group', () => {
    const graph = makeGraph();
    graph.addNode(`${SOURCE_GROUP_PREFIX}s1`, attrs(`${SOURCE_GROUP_PREFIX}s1`, {}));
    const { sigma, view, onToggleSourceGroup, onOpenNode } = renderCanvas(graph);

    doubleClick(sigma, `${SOURCE_GROUP_PREFIX}s1`);
    expect(onToggleSourceGroup).toHaveBeenCalledWith('s1');
    expect(onOpenNode).not.toHaveBeenCalled();
    expect(view.result.current.isExpanded(UNRESOLVED_BUCKET_ID)).toBe(false);
  });
});
