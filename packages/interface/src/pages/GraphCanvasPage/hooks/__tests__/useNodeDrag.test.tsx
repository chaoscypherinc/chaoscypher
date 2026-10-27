// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import Graph from 'graphology';
import type { SigmaNodeEventPayload } from 'sigma/types';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import { SOURCE_GROUP_PREFIX, unresolvedBucketId } from '../../types';
import { useNodeDrag } from '../useNodeDrag';
import { addSourceGroupNode } from '../../utils/transformers';
import { syncUnresolvedBuckets } from '../../utils/unresolvedBucket';

/** The hub for unresolved nodes with no known source. */
const UNRESOLVED_BUCKET_ID = unresolvedBucketId(null);

type Handler = (payload: unknown) => void;
const holder = vi.hoisted(() => ({ sigma: null as unknown }));
vi.mock('@react-sigma/core', () => ({ useSigma: () => holder.sigma }));

function attrs(id: string, content: Record<string, unknown>, x: number, y: number): NodeAttributes {
  return {
    nodeId: id, title: id, content, templateId: 't', tags: [], createdAt: '', updatedAt: '',
    x, y, size: 5, color: '#00E5FF', label: id,
  };
}

function renderDrag(graph: Graph<NodeAttributes, EdgeAttributes>) {
  const handlers = new Map<string, Handler>();
  const container = document.createElement('div');
  holder.sigma = {
    getGraph: () => graph,
    getCamera: () => ({ disable: vi.fn(), enable: vi.fn() }),
    getContainer: () => container,
    viewportToGraph: (p: { x: number; y: number }) => p,
    on: (event: string, handler: Handler) => handlers.set(event, handler),
    off: vi.fn(),
  };
  renderHook(() => useNodeDrag());
  const drag = (node: string, to: { x: number; y: number }) => {
    handlers.get('downNode')?.({ node, preventSigmaDefault: vi.fn() } as unknown as SigmaNodeEventPayload);
    container.dispatchEvent(new MouseEvent('mousemove', { clientX: to.x, clientY: to.y }));
    container.dispatchEvent(new MouseEvent('mouseup'));
  };
  return { drag };
}

describe('useNodeDrag', () => {
  it('dragging the Unresolved hub moves its members with it, and nothing else', () => {
    const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: 'directed' });
    graph.addNode('m1', attrs('m1', { orphan: true }, 10, 10));
    graph.addNode('m2', attrs('m2', { orphan: true }, 30, 10));
    graph.addNode('other', attrs('other', {}, 100, 100));
    syncUnresolvedBuckets(graph); // hub at (20, 10)
    const { drag } = renderDrag(graph);

    drag(UNRESOLVED_BUCKET_ID, { x: 70, y: 60 });

    expect(graph.getNodeAttribute(UNRESOLVED_BUCKET_ID, 'x')).toBe(70);
    expect(graph.getNodeAttributes('m1')).toMatchObject({ x: 60, y: 60 });
    expect(graph.getNodeAttributes('m2')).toMatchObject({ x: 80, y: 60 });
    expect(graph.getNodeAttributes('other')).toMatchObject({ x: 100, y: 100 });
  });

  it('dragging a member moves only that member', () => {
    const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: 'directed' });
    graph.addNode('m1', attrs('m1', { orphan: true }, 10, 10));
    graph.addNode('m2', attrs('m2', { orphan: true }, 30, 10));
    syncUnresolvedBuckets(graph);
    const { drag } = renderDrag(graph);

    drag('m1', { x: 0, y: 0 });

    expect(graph.getNodeAttributes('m1')).toMatchObject({ x: 0, y: 0 });
    expect(graph.getNodeAttributes('m2')).toMatchObject({ x: 30, y: 10 });
    expect(graph.getNodeAttribute(UNRESOLVED_BUCKET_ID, 'x')).toBe(20);
  });

  it('dragging a document root takes its Unresolved hub and the hub\'s members along', () => {
    const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: 'directed' });
    graph.addNode('real', { ...attrs('real', {}, 0, 0), sourceDocumentId: 's1' });
    graph.addNode('u1', { ...attrs('u1', { orphan: true }, 40, 0), sourceDocumentId: 's1' });
    addSourceGroupNode(graph, {
      source_id: 's1', title: 'Doc', source_type: 'text', filename: 'd.txt',
      entity_count: 1, entity_node_ids: ['real'],
    }); // root at (0, 0); u1 is not a cited member, only its home is s1
    syncUnresolvedBuckets(graph); // hub at (40, 0)
    const { drag } = renderDrag(graph);

    drag(`${SOURCE_GROUP_PREFIX}s1`, { x: 10, y: 5 });

    expect(graph.getNodeAttributes(unresolvedBucketId('s1'))).toMatchObject({ x: 50, y: 5 });
    expect(graph.getNodeAttributes('u1')).toMatchObject({ x: 50, y: 5 });
    expect(graph.getNodeAttributes('real')).toMatchObject({ x: 10, y: 5 });
  });
});
