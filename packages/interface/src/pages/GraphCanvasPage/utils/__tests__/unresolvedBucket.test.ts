// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import {
  SOURCE_GROUP_PREFIX,
  isSyntheticEdge,
  isSyntheticNode,
  isUnresolvedBucketNode,
  unresolvedBucketId,
} from '../../types';
import {
  UNRESOLVED_BUCKET_HINT,
  bucketSourceOf,
  groupUnresolvedBySource,
  hoverDetailLines,
  layoutUnresolvedBuckets,
  syncUnresolvedBuckets,
  unresolvedBucketLabel,
  unresolvedEdgeId,
  unresolvedHiddenIds,
  unresolvedMemberIds,
  unresolvedRootEdgeId,
} from '../unresolvedBucket';
import { HUB_NODE_SIZE, UNRESOLVED_HUB_SIZE, addProvenanceEdges, addSourceGroupNode } from '../transformers';
import { ChaosCypherNeutrals } from '../../../../theme/palette';
import { getIconDataUri } from '../../../../utils/iconSprites';

function attrs(id: string, content: Record<string, unknown>, x = 0, y = 0): NodeAttributes {
  return {
    nodeId: id, title: id, content, templateId: 't', tags: [], createdAt: '', updatedAt: '',
    x, y, size: 5, color: '#00E5FF', label: id,
  };
}

function edge(id: string, source: string, target: string): EdgeAttributes {
  return {
    edgeId: id, label: '', templateId: 't', sourceId: source, targetId: target,
    properties: {}, createdAt: '', updatedAt: '',
  };
}

const NO_SOURCE_HUB = unresolvedBucketId(null);

/** plain + connected, two flagged (one with a string "true", which is no flag). */
function makeGraph() {
  const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: 'directed' });
  graph.addNode('plain', attrs('plain', { description: 'named and connected' }, 0, 0));
  graph.addNode('orphan', attrs('orphan', { orphan: true }, 10, 20));
  graph.addNode('stringy', attrs('stringy', { orphan: 'true' }, 0, 0));
  graph.addNode('ref', attrs('ref', { descriptor_reference: true }, 30, 40));
  graph.addEdgeWithKey('e1', 'plain', 'ref', edge('e1', 'plain', 'ref'));
  return graph;
}

describe('unresolvedMemberIds', () => {
  it('lists every real node carrying a pipeline flag, in graph order', () => {
    expect(unresolvedMemberIds(makeGraph())).toEqual(['orphan', 'ref']);
  });

  it('never counts a synthetic node, whatever its content', () => {
    const graph = makeGraph();
    graph.addNode(`${SOURCE_GROUP_PREFIX}s1`, attrs(`${SOURCE_GROUP_PREFIX}s1`, { orphan: true }));
    syncUnresolvedBuckets(graph);
    expect(unresolvedMemberIds(graph)).toEqual(['orphan', 'ref']);
  });
});

describe('syncUnresolvedBuckets (no known source)', () => {
  it('builds one "Unresolved (N)" hub with no document link, shaped like a source root, in a muted grey with a question mark', () => {
    const graph = makeGraph();
    syncUnresolvedBuckets(graph);

    expect(graph.hasNode(NO_SOURCE_HUB)).toBe(true);
    const hub = graph.getNodeAttributes(NO_SOURCE_HUB);
    expect(hub.label).toBe('Unresolved (2)');
    expect(hub.title).toBe('Unresolved');
    expect(hub.size).toBe(UNRESOLVED_HUB_SIZE);
    expect(hub.color).toBe(ChaosCypherNeutrals.textTertiary);
    expect(hub.image).toBe(getIconDataUri('QuestionMark'));
    expect(hub.type).toBe('pictogram');
    expect(hub.content).toEqual({});
    expect(graph.edges(NO_SOURCE_HUB).some((e) => e.startsWith('ur:'))).toBe(false);
  });

  it('is half the size of a source root', () => {
    const graph = makeGraph();
    addSourceGroupNode(graph, {
      source_id: 's1', title: 'Doc', source_type: 'text', filename: 'doc.txt',
      entity_count: 1, entity_node_ids: ['plain'],
    });
    syncUnresolvedBuckets(graph);
    expect(graph.getNodeAttribute(NO_SOURCE_HUB, 'size')).toBe(
      graph.getNodeAttribute(`${SOURCE_GROUP_PREFIX}s1`, 'size') / 2,
    );
    expect(UNRESOLVED_HUB_SIZE).toBe(HUB_NODE_SIZE / 2);
  });

  it('starts the hub at the centroid of its members', () => {
    const graph = makeGraph();
    syncUnresolvedBuckets(graph);
    expect(graph.getNodeAttribute(NO_SOURCE_HUB, 'x')).toBe(20);
    expect(graph.getNodeAttribute(NO_SOURCE_HUB, 'y')).toBe(30);
  });

  it('links each member to the hub with one synthetic edge', () => {
    const graph = makeGraph();
    syncUnresolvedBuckets(graph);

    const hubEdges = graph.edges(NO_SOURCE_HUB);
    expect(hubEdges.sort()).toEqual([unresolvedEdgeId('orphan'), unresolvedEdgeId('ref')].sort());
    for (const id of hubEdges) {
      expect(isSyntheticEdge(id)).toBe(true);
      expect(graph.source(id)).toBe(NO_SOURCE_HUB);
    }
    // The member's real edges are untouched.
    expect(graph.hasEdge('e1')).toBe(true);
  });

  it('adds nothing when no node is unresolved', () => {
    const graph = new Graph<NodeAttributes, EdgeAttributes>();
    graph.addNode('a', attrs('a', {}));
    syncUnresolvedBuckets(graph);
    expect(graph.hasNode(NO_SOURCE_HUB)).toBe(false);
    expect(graph.order).toBe(1);
    expect(graph.size).toBe(0);
  });

  it('is idempotent: a second sync changes nothing', () => {
    const graph = makeGraph();
    syncUnresolvedBuckets(graph);
    const events: string[] = [];
    for (const name of ['nodeAdded', 'nodeDropped', 'edgeAdded', 'edgeDropped', 'nodeAttributesUpdated'] as const) {
      graph.on(name, () => events.push(name));
    }
    syncUnresolvedBuckets(graph);
    expect(events).toEqual([]);
  });

  it('follows membership: recounts, links new members, unlinks resolved ones, keeps its position', () => {
    const graph = makeGraph();
    syncUnresolvedBuckets(graph);
    graph.setNodeAttribute(NO_SOURCE_HUB, 'x', 500);

    graph.setNodeAttribute('plain', 'content', { low_plausibility: true });
    graph.setNodeAttribute('orphan', 'content', {});
    syncUnresolvedBuckets(graph);

    expect(graph.getNodeAttribute(NO_SOURCE_HUB, 'label')).toBe('Unresolved (2)');
    expect(graph.hasEdge(unresolvedEdgeId('plain'))).toBe(true);
    expect(graph.hasEdge(unresolvedEdgeId('orphan'))).toBe(false);
    expect(graph.getNodeAttribute(NO_SOURCE_HUB, 'x')).toBe(500);

    graph.dropNode('ref');
    syncUnresolvedBuckets(graph);
    expect(graph.getNodeAttribute(NO_SOURCE_HUB, 'label')).toBe('Unresolved (1)');
  });

  it('removes the hub and its edges once the last member is gone', () => {
    const graph = makeGraph();
    syncUnresolvedBuckets(graph);
    graph.dropNode('orphan');
    graph.dropNode('ref');
    syncUnresolvedBuckets(graph);
    expect(graph.hasNode(NO_SOURCE_HUB)).toBe(false);
    expect(graph.edges().some(isSyntheticEdge)).toBe(false);
  });

  it('labels the hub with its count', () => {
    expect(unresolvedBucketLabel(7)).toBe('Unresolved (7)');
  });
});

describe('unresolvedHiddenIds', () => {
  const hubs = new Map([
    [unresolvedBucketId('s1'), ['a', 'b']],
    [unresolvedBucketId('s2'), ['c']],
  ]);

  it('collapsed hubs hide their members; each hub on its own', () => {
    expect(unresolvedHiddenIds(hubs, new Set())).toEqual(new Set(['a', 'b', 'c']));
    expect(unresolvedHiddenIds(hubs, new Set([unresolvedBucketId('s1')]))).toEqual(new Set(['c']));
    expect(unresolvedHiddenIds(hubs, new Set(hubs.keys()))).toEqual(new Set());
  });

  it('hides a hub, open or not, with its members when its document is collapsed', () => {
    expect(unresolvedHiddenIds(hubs, new Set(hubs.keys()), new Set(['s1']))).toEqual(
      new Set([unresolvedBucketId('s1'), 'a', 'b']),
    );
  });
});

describe('layoutUnresolvedBuckets', () => {
  const others = [
    { x: 0, y: 0 },
    { x: 100, y: 0 },
    { x: 0, y: 200 },
    { x: 100, y: 200 },
  ];
  const S1 = unresolvedBucketId('s1');
  const S2 = unresolvedBucketId('s2');

  it('sets each hub next to its anchor (its document root) with its members around it', () => {
    const groups = new Map([[S1, ['a', 'b']], [S2, ['c']]]);
    const anchors = new Map([[S1, { x: 0, y: 0 }], [S2, { x: 100, y: 200 }]]);
    const positions = layoutUnresolvedBuckets(groups, anchors, others);
    const h1 = positions.get(S1)!;
    const h2 = positions.get(S2)!;
    expect(Math.hypot(h1.x - 0, h1.y - 0)).toBeLessThan(150);
    expect(Math.hypot(h2.x - 100, h2.y - 200)).toBeLessThan(150);
    expect(h1).not.toEqual({ x: 0, y: 0 }); // beside the root, not on it
    for (const id of ['a', 'b']) {
      const p = positions.get(id)!;
      expect(Math.hypot(p.x - h1.x, p.y - h1.y)).toBeLessThan(100);
    }
    const c = positions.get('c')!;
    expect(Math.hypot(c.x - h2.x, c.y - h2.y)).toBeLessThan(100);
  });

  it('parks a hub with no anchor to the right of the graph, vertically centred', () => {
    const positions = layoutUnresolvedBuckets(new Map([[NO_SOURCE_HUB, ['a', 'b', 'c']]]), new Map(), others);
    const hub = positions.get(NO_SOURCE_HUB)!;
    expect(hub.y).toBeCloseTo(100);
    for (const id of ['a', 'b', 'c']) {
      expect(positions.get(id)!.x).toBeGreaterThan(100);
    }
  });

  it('keeps two unanchored hubs apart', () => {
    const groups = new Map([[NO_SOURCE_HUB, ['a']], [S1, ['b']]]);
    const positions = layoutUnresolvedBuckets(groups, new Map(), others);
    const a = positions.get(NO_SOURCE_HUB)!;
    const b = positions.get(S1)!;
    expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThan(48);
  });

  it('lays the members out around the hub, none on top of it or of each other', () => {
    const ids = Array.from({ length: 40 }, (_, i) => `m${i}`);
    const spacing = 24;
    const positions = layoutUnresolvedBuckets(new Map([[S1, ids]]), new Map([[S1, { x: 0, y: 0 }]]), others, spacing);
    const hub = positions.get(S1)!;
    const points = ids.map((id) => positions.get(id)!);
    for (const p of points) {
      expect(Math.hypot(p.x - hub.x, p.y - hub.y)).toBeGreaterThanOrEqual(spacing - 1e-9);
    }
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        const d = Math.hypot(points[i].x - points[j].x, points[i].y - points[j].y);
        expect(d).toBeGreaterThan(spacing * 0.5);
      }
    }
    const cx = points.reduce((sum, p) => sum + p.x, 0) / points.length;
    const cy = points.reduce((sum, p) => sum + p.y, 0) / points.length;
    expect(Math.hypot(cx - hub.x, cy - hub.y)).toBeLessThan(spacing);
  });

  it('centres an unanchored hub on the origin when nothing else is on the canvas', () => {
    const positions = layoutUnresolvedBuckets(new Map([[NO_SOURCE_HUB, ['a']]]), new Map(), []);
    expect(positions.get(NO_SOURCE_HUB)).toEqual({ x: 0, y: 0 });
  });

  it('places nothing for no hubs', () => {
    expect(layoutUnresolvedBuckets(new Map(), new Map(), others).size).toBe(0);
  });
});

describe('hoverDetailLines', () => {
  it('says why a node is unresolved, one plain line per reason', () => {
    expect(hoverDetailLines('n1', { orphan: true, low_plausibility: true })).toEqual([
      'No connections to other nodes',
      'Its name may not be a real entity',
    ]);
  });

  it('tells how to open or close the Unresolved hub', () => {
    expect(hoverDetailLines(NO_SOURCE_HUB, {})).toEqual([UNRESOLVED_BUCKET_HINT]);
  });

  it('adds nothing for an ordinary node', () => {
    expect(hoverDetailLines('n1', { description: 'x', orphan: 'true' })).toEqual([]);
    expect(hoverDetailLines('n1', undefined)).toEqual([]);
  });
});

// ── One hub per source document ─────────────────────────────────────

/** Two documents: s1 (with two unresolved nodes), s2 (one), plus a no-source one. */
function makeSourcedGraph() {
  const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: 'directed' });
  const add = (id: string, content: Record<string, unknown>, source?: string) =>
    graph.addNode(id, { ...attrs(id, content), sourceDocumentId: source });
  add('a1', {}, 's1');
  add('u1', { orphan: true }, 's1');
  add('u2', { low_plausibility: true }, 's1');
  add('b1', {}, 's2');
  add('u3', { orphan: true }, 's2');
  add('loose', { orphan: true });
  const group = (sourceId: string, ids: string[]) => {
    addSourceGroupNode(graph, {
      source_id: sourceId, title: sourceId, source_type: 'text', filename: `${sourceId}.txt`,
      entity_count: ids.length, entity_node_ids: ids,
    });
    addProvenanceEdges(graph, sourceId, ids);
  };
  group('s1', ['a1', 'u1', 'u2']);
  group('s2', ['b1', 'u3']);
  return graph;
}

describe('syncUnresolvedBuckets (one hub per source)', () => {
  it('builds one hub per source document, counting that document\'s unresolved nodes', () => {
    const graph = makeSourcedGraph();
    const hubs = syncUnresolvedBuckets(graph);

    expect(hubs).toEqual(new Map([
      [unresolvedBucketId('s1'), ['u1', 'u2']],
      [unresolvedBucketId('s2'), ['u3']],
      [NO_SOURCE_HUB, ['loose']],
    ]));
    expect(graph.getNodeAttribute(unresolvedBucketId('s1'), 'label')).toBe('Unresolved (2)');
    expect(graph.getNodeAttribute(unresolvedBucketId('s2'), 'label')).toBe('Unresolved (1)');
    expect(graph.getNodeAttribute(NO_SOURCE_HUB, 'label')).toBe('Unresolved (1)');
    // Each member is linked to its own source's hub only.
    expect(graph.source(unresolvedEdgeId('u1'))).toBe(unresolvedBucketId('s1'));
    expect(graph.source(unresolvedEdgeId('u3'))).toBe(unresolvedBucketId('s2'));
    expect(graph.source(unresolvedEdgeId('loose'))).toBe(NO_SOURCE_HUB);
  });

  it('links each hub to its document root with a synthetic edge; the no-source hub has none', () => {
    const graph = makeSourcedGraph();
    syncUnresolvedBuckets(graph);

    for (const source of ['s1', 's2']) {
      const edgeId = unresolvedRootEdgeId(source);
      expect(graph.hasEdge(edgeId)).toBe(true);
      expect(graph.source(edgeId)).toBe(`${SOURCE_GROUP_PREFIX}${source}`);
      expect(graph.target(edgeId)).toBe(unresolvedBucketId(source));
      expect(isSyntheticEdge(edgeId)).toBe(true);
    }
    expect(graph.inEdges(NO_SOURCE_HUB)).toEqual([]);
  });

  it('adds the root link once the document root appears, keeping the hub', () => {
    const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: 'directed' });
    graph.addNode('u1', { ...attrs('u1', { orphan: true }), sourceDocumentId: 's1' });
    syncUnresolvedBuckets(graph);
    expect(graph.hasNode(unresolvedBucketId('s1'))).toBe(true);
    expect(graph.hasEdge(unresolvedRootEdgeId('s1'))).toBe(false);

    addSourceGroupNode(graph, {
      source_id: 's1', title: 's1', source_type: 'text', filename: 's1.txt',
      entity_count: 1, entity_node_ids: ['u1'],
    });
    syncUnresolvedBuckets(graph);
    expect(graph.hasEdge(unresolvedRootEdgeId('s1'))).toBe(true);
  });

  it('drops a source\'s hub when its document leaves the canvas (a source filter)', () => {
    const graph = makeSourcedGraph();
    syncUnresolvedBuckets(graph);
    for (const id of ['b1', 'u3', `${SOURCE_GROUP_PREFIX}s2`]) graph.dropNode(id);
    syncUnresolvedBuckets(graph);
    expect(graph.hasNode(unresolvedBucketId('s2'))).toBe(false);
    expect(graph.hasNode(unresolvedBucketId('s1'))).toBe(true);
  });

  it('moves a member whose source changes to the new source\'s hub', () => {
    const graph = makeSourcedGraph();
    syncUnresolvedBuckets(graph);
    graph.setNodeAttribute('loose', 'sourceDocumentId', 's2');
    const hubs = syncUnresolvedBuckets(graph);
    expect(hubs.get(unresolvedBucketId('s2'))).toEqual(['u3', 'loose']);
    expect(graph.source(unresolvedEdgeId('loose'))).toBe(unresolvedBucketId('s2'));
    expect(graph.hasNode(NO_SOURCE_HUB)).toBe(false);
  });

  it('is idempotent with several hubs', () => {
    const graph = makeSourcedGraph();
    syncUnresolvedBuckets(graph);
    const events: string[] = [];
    for (const name of ['nodeAdded', 'nodeDropped', 'edgeAdded', 'edgeDropped', 'nodeAttributesUpdated'] as const) {
      graph.on(name, () => events.push(name));
    }
    syncUnresolvedBuckets(graph);
    expect(events).toEqual([]);
  });

  it('marks every hub and hub edge as synthetic, and never makes a hub a member', () => {
    const graph = makeSourcedGraph();
    syncUnresolvedBuckets(graph);
    for (const hub of [unresolvedBucketId('s1'), unresolvedBucketId('s2'), NO_SOURCE_HUB]) {
      expect(isSyntheticNode(hub)).toBe(true);
      expect(isUnresolvedBucketNode(hub)).toBe(true);
      for (const e of graph.edges(hub)) expect(isSyntheticEdge(e)).toBe(true);
    }
    expect(unresolvedMemberIds(graph)).toEqual(['u1', 'u2', 'u3', 'loose']);
  });
});

describe('bucketSourceOf (which source a node hangs off)', () => {
  it('takes the node\'s home source first, even when another document also cites it', () => {
    const graph = makeSourcedGraph();
    // u3's home is s2, and s1 cites it too (s1's root loaded first).
    addProvenanceEdges(graph, 's1', ['u3']);
    expect(bucketSourceOf(graph, 'u3')).toBe('s2');
  });

  it('without a home source, takes the first document root it hangs off', () => {
    const graph = makeSourcedGraph();
    addProvenanceEdges(graph, 's1', ['loose']);
    addProvenanceEdges(graph, 's2', ['loose']);
    expect(bucketSourceOf(graph, 'loose')).toBe('s1');
  });

  it('with neither, has no source (the no-source hub)', () => {
    expect(bucketSourceOf(makeSourcedGraph(), 'loose')).toBeNull();
    expect(groupUnresolvedBySource(makeSourcedGraph()).get(NO_SOURCE_HUB)).toEqual(['loose']);
  });
});
