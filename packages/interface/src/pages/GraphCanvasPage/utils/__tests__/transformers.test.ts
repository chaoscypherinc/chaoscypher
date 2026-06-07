// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, beforeEach } from 'vitest';
import Graph from 'graphology';
import type { Node as ApiNode, Edge as ApiEdge, Template } from '../../../../types';
import type { SourceGroup } from '../../../../types/graph';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import { SOURCE_GROUP_PREFIX, SOURCE_PROVENANCE_PREFIX } from '../../types';
import {
  populateGraphFromApi,
  addApiNodeToGraph,
  addApiEdgeToGraph,
  applyDegreeSizing,
  addSourceGroupNode,
  addProvenanceEdges,
} from '../transformers';

// ========================================
// Fixture helpers
// ========================================

function makeApiNode(overrides: Partial<ApiNode> & { id: string; template_id: string }): ApiNode {
  return {
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeApiEdge(
  overrides: Partial<ApiEdge> & {
    id: string;
    template_id: string;
    source_node_id: string;
    target_node_id: string;
  },
): ApiEdge {
  return {
    created_at: '2024-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeTemplate(id: string, overrides: Partial<Template> = {}): Template {
  return {
    id,
    name: `Template ${id}`,
    template_type: 'node',
    properties: [],
    is_system: false,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    color: '#FF0000',
    icon: null,
    ...overrides,
  };
}

function makeSourceGroup(overrides: Partial<SourceGroup> & { source_id: string }): SourceGroup {
  return {
    title: 'Test Source',
    source_type: 'pdf',
    filename: 'test.pdf',
    entity_count: 0,
    entity_node_ids: [],
    ...overrides,
  };
}

// ========================================
// Tests: populateGraphFromApi
// ========================================

describe('populateGraphFromApi', () => {
  let graph: Graph<NodeAttributes, EdgeAttributes>;

  beforeEach(() => {
    graph = new Graph();
  });

  it('clears existing graph content before populating', () => {
    graph.addNode('stale-node', {
      nodeId: 'stale-node',
      title: 'Old',
      content: {},
      templateId: 'x',
      tags: [],
      createdAt: '',
      updatedAt: '',
      x: 0,
      y: 0,
      size: 8,
      color: '#000',
      label: 'Old',
    });
    populateGraphFromApi(graph, [], [], undefined);
    expect(graph.order).toBe(0);
  });

  it('adds all nodes from apiNodes', () => {
    const nodes: ApiNode[] = [
      makeApiNode({ id: 'n1', template_id: 'tmpl-a' }),
      makeApiNode({ id: 'n2', template_id: 'tmpl-b' }),
    ];
    populateGraphFromApi(graph, nodes, [], undefined);
    expect(graph.order).toBe(2);
    expect(graph.hasNode('n1')).toBe(true);
    expect(graph.hasNode('n2')).toBe(true);
  });

  it('adds all edges whose endpoints exist', () => {
    const nodes: ApiNode[] = [
      makeApiNode({ id: 'n1', template_id: 'tmpl-a' }),
      makeApiNode({ id: 'n2', template_id: 'tmpl-b' }),
    ];
    const edges: ApiEdge[] = [
      makeApiEdge({ id: 'e1', template_id: 'tmpl-e', source_node_id: 'n1', target_node_id: 'n2' }),
    ];
    populateGraphFromApi(graph, nodes, edges, undefined);
    expect(graph.size).toBe(1);
    expect(graph.hasEdge('e1')).toBe(true);
  });

  it('skips edges whose source node is missing', () => {
    const nodes: ApiNode[] = [makeApiNode({ id: 'n2', template_id: 'tmpl-b' })];
    const edges: ApiEdge[] = [
      makeApiEdge({ id: 'e1', template_id: 'tmpl-e', source_node_id: 'missing', target_node_id: 'n2' }),
    ];
    populateGraphFromApi(graph, nodes, edges, undefined);
    expect(graph.size).toBe(0);
  });

  it('skips edges whose target node is missing', () => {
    const nodes: ApiNode[] = [makeApiNode({ id: 'n1', template_id: 'tmpl-a' })];
    const edges: ApiEdge[] = [
      makeApiEdge({ id: 'e1', template_id: 'tmpl-e', source_node_id: 'n1', target_node_id: 'missing' }),
    ];
    populateGraphFromApi(graph, nodes, edges, undefined);
    expect(graph.size).toBe(0);
  });

  it('handles empty input without throwing', () => {
    expect(() => populateGraphFromApi(graph, [], [], undefined)).not.toThrow();
    expect(graph.order).toBe(0);
    expect(graph.size).toBe(0);
  });

  it('uses node title as label', () => {
    const nodes: ApiNode[] = [
      makeApiNode({ id: 'n1', template_id: 'tmpl-a', title: 'My Title' }),
    ];
    populateGraphFromApi(graph, nodes, [], undefined);
    const attrs = graph.getNodeAttributes('n1');
    expect(attrs.label).toBe('My Title');
    expect(attrs.title).toBe('My Title');
  });

  it('falls back to label field when title is absent', () => {
    const nodes: ApiNode[] = [
      makeApiNode({ id: 'n1', template_id: 'tmpl-a', label: 'From Label' }),
    ];
    populateGraphFromApi(graph, nodes, [], undefined);
    const attrs = graph.getNodeAttributes('n1');
    expect(attrs.label).toBe('From Label');
  });

  it('falls back to Untitled when neither title nor label is provided', () => {
    const nodes: ApiNode[] = [makeApiNode({ id: 'n1', template_id: 'tmpl-a' })];
    populateGraphFromApi(graph, nodes, [], undefined);
    expect(graph.getNodeAttribute('n1', 'label')).toBe('Untitled');
  });

  it('uses template color from templateMap when provided', () => {
    const nodes: ApiNode[] = [makeApiNode({ id: 'n1', template_id: 'tmpl-a' })];
    const tpl = makeTemplate('tmpl-a', { color: '#ABCDEF' });
    const templateMap = new Map<string, Template>([['tmpl-a', tpl]]);
    populateGraphFromApi(graph, nodes, [], templateMap);
    expect(graph.getNodeAttribute('n1', 'color')).toBe('#ABCDEF');
  });

  it('falls back to getColorForTemplate when no templateMap entry', () => {
    const nodes: ApiNode[] = [makeApiNode({ id: 'n1', template_id: 'some-unknown-id' })];
    populateGraphFromApi(graph, nodes, [], undefined);
    const color = graph.getNodeAttribute('n1', 'color');
    expect(typeof color).toBe('string');
    expect(color.length).toBeGreaterThan(0);
  });

  it('stores templateId on node attributes', () => {
    const nodes: ApiNode[] = [makeApiNode({ id: 'n1', template_id: 'tmpl-xyz' })];
    populateGraphFromApi(graph, nodes, [], undefined);
    expect(graph.getNodeAttribute('n1', 'templateId')).toBe('tmpl-xyz');
  });

  it('uses default template id when node has no template_id', () => {
    const nodes: ApiNode[] = [makeApiNode({ id: 'n1', template_id: '' })];
    // template_id falsy → 'default'
    populateGraphFromApi(graph, nodes, [], undefined);
    expect(graph.getNodeAttribute('n1', 'templateId')).toBe('default');
  });

  it('stores node position when provided', () => {
    const nodes: ApiNode[] = [
      makeApiNode({ id: 'n1', template_id: 'tmpl-a', position: { x: 100, y: 200 } }),
    ];
    populateGraphFromApi(graph, nodes, [], undefined);
    expect(graph.getNodeAttribute('n1', 'x')).toBe(100);
    expect(graph.getNodeAttribute('n1', 'y')).toBe(200);
  });

  it('sets default size on nodes', () => {
    const nodes: ApiNode[] = [makeApiNode({ id: 'n1', template_id: 'tmpl-a' })];
    populateGraphFromApi(graph, nodes, [], undefined);
    expect(graph.getNodeAttribute('n1', 'size')).toBe(8);
  });

  it('stores edge attributes correctly', () => {
    const nodes: ApiNode[] = [
      makeApiNode({ id: 'n1', template_id: 'tmpl-a' }),
      makeApiNode({ id: 'n2', template_id: 'tmpl-b' }),
    ];
    const edges: ApiEdge[] = [
      makeApiEdge({
        id: 'e1',
        template_id: 'tmpl-e',
        source_node_id: 'n1',
        target_node_id: 'n2',
        label: 'relates to',
      }),
    ];
    populateGraphFromApi(graph, nodes, edges, undefined);
    const attrs = graph.getEdgeAttributes('e1');
    expect(attrs.edgeId).toBe('e1');
    expect(attrs.label).toBe('relates to');
    expect(attrs.sourceId).toBe('n1');
    expect(attrs.targetId).toBe('n2');
  });

  it('skips duplicate edges silently', () => {
    const nodes: ApiNode[] = [
      makeApiNode({ id: 'n1', template_id: 'tmpl-a' }),
      makeApiNode({ id: 'n2', template_id: 'tmpl-b' }),
    ];
    const edges: ApiEdge[] = [
      makeApiEdge({ id: 'e1', template_id: 'tmpl-e', source_node_id: 'n1', target_node_id: 'n2' }),
      makeApiEdge({ id: 'e1', template_id: 'tmpl-e', source_node_id: 'n1', target_node_id: 'n2' }),
    ];
    expect(() => populateGraphFromApi(graph, nodes, edges, undefined)).not.toThrow();
    expect(graph.size).toBe(1);
  });

  it('uses edge template color from templateMap', () => {
    const nodes: ApiNode[] = [
      makeApiNode({ id: 'n1', template_id: 'tmpl-a' }),
      makeApiNode({ id: 'n2', template_id: 'tmpl-b' }),
    ];
    const edges: ApiEdge[] = [
      makeApiEdge({ id: 'e1', template_id: 'tmpl-e', source_node_id: 'n1', target_node_id: 'n2' }),
    ];
    const edgeTpl = makeTemplate('tmpl-e', { color: '#112233', template_type: 'edge' });
    const templateMap = new Map<string, Template>([['tmpl-e', edgeTpl]]);
    populateGraphFromApi(graph, nodes, edges, templateMap);
    expect(graph.getEdgeAttribute('e1', 'color')).toBe('#112233');
  });

  it('stores source_id as sourceDocumentId when present', () => {
    const nodes: ApiNode[] = [
      makeApiNode({ id: 'n1', template_id: 'tmpl-a', source_id: 'src-42' }),
    ];
    populateGraphFromApi(graph, nodes, [], undefined);
    expect(graph.getNodeAttribute('n1', 'sourceDocumentId')).toBe('src-42');
  });
});

// ========================================
// Tests: addApiNodeToGraph
// ========================================

describe('addApiNodeToGraph', () => {
  let graph: Graph<NodeAttributes, EdgeAttributes>;

  beforeEach(() => {
    graph = new Graph();
  });

  it('adds a single node to an existing graph', () => {
    const node = makeApiNode({ id: 'n1', template_id: 'tmpl-a', title: 'Hello' });
    addApiNodeToGraph(graph, node, undefined, undefined);
    expect(graph.hasNode('n1')).toBe(true);
    expect(graph.getNodeAttribute('n1', 'title')).toBe('Hello');
  });

  it('does not add a duplicate node (already present)', () => {
    const node = makeApiNode({ id: 'n1', template_id: 'tmpl-a', title: 'First' });
    addApiNodeToGraph(graph, node, undefined, undefined);
    const node2 = makeApiNode({ id: 'n1', template_id: 'tmpl-a', title: 'Second' });
    addApiNodeToGraph(graph, node2, undefined, undefined);
    expect(graph.order).toBe(1);
    // First insertion survives
    expect(graph.getNodeAttribute('n1', 'title')).toBe('First');
  });

  it('uses provided position override', () => {
    const node = makeApiNode({ id: 'n1', template_id: 'tmpl-a' });
    addApiNodeToGraph(graph, node, { x: 42, y: 99 }, undefined);
    expect(graph.getNodeAttribute('n1', 'x')).toBe(42);
    expect(graph.getNodeAttribute('n1', 'y')).toBe(99);
  });

  it('falls back to node.position when position param is omitted', () => {
    const node = makeApiNode({ id: 'n1', template_id: 'tmpl-a', position: { x: 10, y: 20 } });
    addApiNodeToGraph(graph, node, undefined, undefined);
    expect(graph.getNodeAttribute('n1', 'x')).toBe(10);
    expect(graph.getNodeAttribute('n1', 'y')).toBe(20);
  });

  it('uses template color from templateMap', () => {
    const tpl = makeTemplate('tmpl-x', { color: '#CAFE00' });
    const templateMap = new Map<string, Template>([['tmpl-x', tpl]]);
    const node = makeApiNode({ id: 'n1', template_id: 'tmpl-x' });
    addApiNodeToGraph(graph, node, undefined, templateMap);
    expect(graph.getNodeAttribute('n1', 'color')).toBe('#CAFE00');
  });

  it('falls back to computed color when templateMap is missing', () => {
    const node = makeApiNode({ id: 'n1', template_id: 'some-template' });
    addApiNodeToGraph(graph, node, undefined, undefined);
    const color = graph.getNodeAttribute('n1', 'color');
    expect(typeof color).toBe('string');
    expect(color.length).toBeGreaterThan(0);
  });

  it('falls back to Untitled when title and label are absent', () => {
    const node = makeApiNode({ id: 'n1', template_id: 'tmpl-a' });
    addApiNodeToGraph(graph, node, undefined, undefined);
    expect(graph.getNodeAttribute('n1', 'label')).toBe('Untitled');
  });

  it('stores tags array on node attributes', () => {
    const node = makeApiNode({ id: 'n1', template_id: 'tmpl-a', tags: ['foo', 'bar'] });
    addApiNodeToGraph(graph, node, undefined, undefined);
    expect(graph.getNodeAttribute('n1', 'tags')).toEqual(['foo', 'bar']);
  });
});

// ========================================
// Tests: addApiEdgeToGraph
// ========================================

describe('addApiEdgeToGraph', () => {
  let graph: Graph<NodeAttributes, EdgeAttributes>;

  beforeEach(() => {
    graph = new Graph();
    // Pre-populate two nodes
    const n1 = makeApiNode({ id: 'n1', template_id: 'tmpl-a' });
    const n2 = makeApiNode({ id: 'n2', template_id: 'tmpl-b' });
    addApiNodeToGraph(graph, n1, { x: 0, y: 0 }, undefined);
    addApiNodeToGraph(graph, n2, { x: 10, y: 10 }, undefined);
  });

  it('adds an edge between two existing nodes', () => {
    const edge = makeApiEdge({
      id: 'e1',
      template_id: 'tmpl-e',
      source_node_id: 'n1',
      target_node_id: 'n2',
    });
    addApiEdgeToGraph(graph, edge, undefined);
    expect(graph.hasEdge('e1')).toBe(true);
  });

  it('returns early when source node is missing', () => {
    const edge = makeApiEdge({
      id: 'e1',
      template_id: 'tmpl-e',
      source_node_id: 'missing',
      target_node_id: 'n2',
    });
    addApiEdgeToGraph(graph, edge, undefined);
    expect(graph.size).toBe(0);
  });

  it('returns early when target node is missing', () => {
    const edge = makeApiEdge({
      id: 'e1',
      template_id: 'tmpl-e',
      source_node_id: 'n1',
      target_node_id: 'missing',
    });
    addApiEdgeToGraph(graph, edge, undefined);
    expect(graph.size).toBe(0);
  });

  it('does not add duplicate edge when same id already exists', () => {
    const edge = makeApiEdge({
      id: 'e1',
      template_id: 'tmpl-e',
      source_node_id: 'n1',
      target_node_id: 'n2',
    });
    addApiEdgeToGraph(graph, edge, undefined);
    addApiEdgeToGraph(graph, edge, undefined);
    expect(graph.size).toBe(1);
  });

  it('stores edge attributes (edgeId, label, sourceId, targetId, templateId)', () => {
    const edge = makeApiEdge({
      id: 'e1',
      template_id: 'tmpl-e',
      source_node_id: 'n1',
      target_node_id: 'n2',
      label: 'connects',
    });
    addApiEdgeToGraph(graph, edge, undefined);
    const attrs = graph.getEdgeAttributes('e1');
    expect(attrs.edgeId).toBe('e1');
    expect(attrs.label).toBe('connects');
    expect(attrs.sourceId).toBe('n1');
    expect(attrs.targetId).toBe('n2');
    expect(attrs.templateId).toBe('tmpl-e');
  });

  it('uses template color from templateMap for edge', () => {
    const edgeTpl = makeTemplate('tmpl-e', { color: '#DEADBE', template_type: 'edge' });
    const templateMap = new Map<string, Template>([['tmpl-e', edgeTpl]]);
    const edge = makeApiEdge({
      id: 'e1',
      template_id: 'tmpl-e',
      source_node_id: 'n1',
      target_node_id: 'n2',
    });
    addApiEdgeToGraph(graph, edge, templateMap);
    expect(graph.getEdgeAttribute('e1', 'color')).toBe('#DEADBE');
  });

  it('has empty label string when edge.label is absent', () => {
    const edge = makeApiEdge({
      id: 'e1',
      template_id: 'tmpl-e',
      source_node_id: 'n1',
      target_node_id: 'n2',
    });
    addApiEdgeToGraph(graph, edge, undefined);
    expect(graph.getEdgeAttribute('e1', 'label')).toBe('');
  });
});

// ========================================
// Tests: applyDegreeSizing
// ========================================

describe('applyDegreeSizing', () => {
  let graph: Graph<NodeAttributes, EdgeAttributes>;

  function addNode(id: string, x = 0, y = 0): void {
    graph.addNode(id, {
      nodeId: id,
      title: id,
      content: {},
      templateId: 'tmpl-a',
      tags: [],
      createdAt: '',
      updatedAt: '',
      x,
      y,
      size: 8,
      color: '#000',
      label: id,
    });
  }

  function addEdge(id: string, source: string, target: string): void {
    graph.addEdgeWithKey(id, source, target, {
      edgeId: id,
      label: '',
      templateId: 'tmpl-e',
      sourceId: source,
      targetId: target,
      properties: {},
      createdAt: '',
      updatedAt: '',
      size: 1,
    });
  }

  beforeEach(() => {
    graph = new Graph();
  });

  it('does not throw on an empty graph', () => {
    expect(() => applyDegreeSizing(graph)).not.toThrow();
  });

  it('sets size=3 for an isolated node (0 degree)', () => {
    addNode('n1');
    applyDegreeSizing(graph);
    // Isolated: degree=0 → t = log2(1)/log2(2) = 0 → size = 3
    expect(graph.getNodeAttribute('n1', 'size')).toBe(3);
  });

  it('hub node gets a larger size than leaf node', () => {
    // Hub: n1 connects to n2, n3, n4, n5
    addNode('hub');
    addNode('leaf1');
    addNode('leaf2');
    addNode('leaf3');
    addNode('leaf4');
    addEdge('e1', 'hub', 'leaf1');
    addEdge('e2', 'hub', 'leaf2');
    addEdge('e3', 'hub', 'leaf3');
    addEdge('e4', 'hub', 'leaf4');
    applyDegreeSizing(graph);
    const hubSize = graph.getNodeAttribute('hub', 'size');
    const leafSize = graph.getNodeAttribute('leaf1', 'size');
    expect(hubSize).toBeGreaterThan(leafSize);
  });

  it('max-degree node gets size = 8 (MAX_SIZE)', () => {
    addNode('hub');
    addNode('n2');
    addNode('n3');
    addEdge('e1', 'hub', 'n2');
    addEdge('e2', 'hub', 'n3');
    // hub has degree 2, n2/n3 have degree 1
    applyDegreeSizing(graph);
    // hub is the max-degree node → size = MAX_SIZE = 8
    expect(graph.getNodeAttribute('hub', 'size')).toBe(8);
  });

  it('skips source group nodes (isSourceGroup=true)', () => {
    addNode('regular');
    // Add a virtual source group node
    graph.addNode('sg:test', {
      nodeId: 'sg:test',
      title: 'Source',
      content: {},
      templateId: '__source_group__',
      tags: [],
      createdAt: '',
      updatedAt: '',
      x: 0,
      y: 0,
      size: 12,
      color: '#gold',
      label: 'Source',
      isSourceGroup: true,
    });
    addEdge('e1', 'regular', 'sg:test');
    applyDegreeSizing(graph);
    // Source group node size should remain unchanged at 12
    expect(graph.getNodeAttribute('sg:test', 'size')).toBe(12);
  });

  it('sizes are within [3, 8] range', () => {
    addNode('n1');
    addNode('n2');
    addNode('n3');
    addEdge('e1', 'n1', 'n2');
    addEdge('e2', 'n1', 'n3');
    applyDegreeSizing(graph);
    for (const nodeId of ['n1', 'n2', 'n3']) {
      const size = graph.getNodeAttribute(nodeId, 'size');
      expect(size).toBeGreaterThanOrEqual(3);
      expect(size).toBeLessThanOrEqual(8);
    }
  });
});

// ========================================
// Tests: addSourceGroupNode
// ========================================

describe('addSourceGroupNode', () => {
  let graph: Graph<NodeAttributes, EdgeAttributes>;

  function addNode(id: string, x: number, y: number): void {
    graph.addNode(id, {
      nodeId: id,
      title: id,
      content: {},
      templateId: 'tmpl-a',
      tags: [],
      createdAt: '',
      updatedAt: '',
      x,
      y,
      size: 8,
      color: '#000',
      label: id,
    });
  }

  beforeEach(() => {
    graph = new Graph();
  });

  it('returns empty array when no member nodes exist in graph', () => {
    const group = makeSourceGroup({
      source_id: 'src1',
      entity_node_ids: ['missing1', 'missing2'],
    });
    const result = addSourceGroupNode(graph, group);
    expect(result).toEqual([]);
    expect(graph.hasNode(`${SOURCE_GROUP_PREFIX}src1`)).toBe(false);
  });

  it('creates a source group node with the correct prefix', () => {
    addNode('n1', 100, 200);
    const group = makeSourceGroup({
      source_id: 'src1',
      title: 'My Source',
      entity_node_ids: ['n1'],
    });
    addSourceGroupNode(graph, group);
    expect(graph.hasNode(`${SOURCE_GROUP_PREFIX}src1`)).toBe(true);
  });

  it('returns only the member node IDs that are present in the graph', () => {
    addNode('n1', 0, 0);
    addNode('n2', 10, 10);
    const group = makeSourceGroup({
      source_id: 'src1',
      entity_node_ids: ['n1', 'n2', 'missing'],
    });
    const result = addSourceGroupNode(graph, group);
    expect(result).toEqual(['n1', 'n2']);
  });

  it('places the group node at the centroid of member nodes', () => {
    addNode('n1', 0, 0);
    addNode('n2', 100, 200);
    const group = makeSourceGroup({
      source_id: 'src1',
      entity_node_ids: ['n1', 'n2'],
    });
    addSourceGroupNode(graph, group);
    const groupAttrs = graph.getNodeAttributes(`${SOURCE_GROUP_PREFIX}src1`);
    expect(groupAttrs.x).toBe(50); // (0+100)/2
    expect(groupAttrs.y).toBe(100); // (0+200)/2
  });

  it('marks member nodes with sourceGroupMembership', () => {
    addNode('n1', 0, 0);
    const group = makeSourceGroup({
      source_id: 'src-abc',
      entity_node_ids: ['n1'],
    });
    addSourceGroupNode(graph, group);
    expect(graph.getNodeAttribute('n1', 'sourceGroupMembership')).toBe('src-abc');
  });

  it('sets isSourceGroup=true on the created node', () => {
    addNode('n1', 0, 0);
    const group = makeSourceGroup({ source_id: 'src1', entity_node_ids: ['n1'] });
    addSourceGroupNode(graph, group);
    expect(graph.getNodeAttribute(`${SOURCE_GROUP_PREFIX}src1`, 'isSourceGroup')).toBe(true);
  });

  it('sets sourceGroupId and sourceGroupEntityCount on group node', () => {
    addNode('n1', 0, 0);
    addNode('n2', 10, 10);
    const group = makeSourceGroup({
      source_id: 'src1',
      entity_node_ids: ['n1', 'n2'],
    });
    addSourceGroupNode(graph, group);
    const attrs = graph.getNodeAttributes(`${SOURCE_GROUP_PREFIX}src1`);
    expect(attrs.sourceGroupId).toBe('src1');
    expect(attrs.sourceGroupEntityCount).toBe(2);
  });

  it('does not create a duplicate group node if called twice', () => {
    addNode('n1', 0, 0);
    const group = makeSourceGroup({ source_id: 'src1', entity_node_ids: ['n1'] });
    addSourceGroupNode(graph, group);
    addSourceGroupNode(graph, group);
    // Only one group node should exist
    let count = 0;
    graph.forEachNode((nodeId) => {
      if (nodeId === `${SOURCE_GROUP_PREFIX}src1`) count++;
    });
    expect(count).toBe(1);
  });

  it('sets the label to include entity count', () => {
    addNode('n1', 0, 0);
    const group = makeSourceGroup({
      source_id: 'src1',
      title: 'My Src',
      entity_node_ids: ['n1'],
    });
    addSourceGroupNode(graph, group);
    const label = graph.getNodeAttribute(`${SOURCE_GROUP_PREFIX}src1`, 'label');
    expect(label).toBe('My Src (1)');
  });
});

// ========================================
// Tests: addProvenanceEdges
// ========================================

describe('addProvenanceEdges', () => {
  let graph: Graph<NodeAttributes, EdgeAttributes>;

  function addNode(id: string): void {
    graph.addNode(id, {
      nodeId: id,
      title: id,
      content: {},
      templateId: 'tmpl-a',
      tags: [],
      createdAt: '',
      updatedAt: '',
      x: 0,
      y: 0,
      size: 8,
      color: '#000',
      label: id,
    });
  }

  beforeEach(() => {
    graph = new Graph();
  });

  it('does nothing when the group node does not exist', () => {
    addNode('n1');
    addProvenanceEdges(graph, 'nonexistent-src', ['n1']);
    expect(graph.size).toBe(0);
  });

  it('creates provenance edges from group node to each member', () => {
    const groupNodeId = `${SOURCE_GROUP_PREFIX}src1`;
    addNode(groupNodeId);
    addNode('n1');
    addNode('n2');
    addProvenanceEdges(graph, 'src1', ['n1', 'n2']);
    expect(graph.size).toBe(2);
    const edgeId1 = `${SOURCE_PROVENANCE_PREFIX}src1:n1`;
    const edgeId2 = `${SOURCE_PROVENANCE_PREFIX}src1:n2`;
    expect(graph.hasEdge(edgeId1)).toBe(true);
    expect(graph.hasEdge(edgeId2)).toBe(true);
  });

  it('edge IDs use SOURCE_PROVENANCE_PREFIX', () => {
    const groupNodeId = `${SOURCE_GROUP_PREFIX}src-xyz`;
    addNode(groupNodeId);
    addNode('member1');
    addProvenanceEdges(graph, 'src-xyz', ['member1']);
    const expectedEdgeId = `${SOURCE_PROVENANCE_PREFIX}src-xyz:member1`;
    expect(graph.hasEdge(expectedEdgeId)).toBe(true);
  });

  it('sets isProvenance=true on each provenance edge', () => {
    const groupNodeId = `${SOURCE_GROUP_PREFIX}src1`;
    addNode(groupNodeId);
    addNode('n1');
    addProvenanceEdges(graph, 'src1', ['n1']);
    const edgeId = `${SOURCE_PROVENANCE_PREFIX}src1:n1`;
    expect(graph.getEdgeAttribute(edgeId, 'isProvenance')).toBe(true);
  });

  it('skips member nodes that do not exist in the graph', () => {
    const groupNodeId = `${SOURCE_GROUP_PREFIX}src1`;
    addNode(groupNodeId);
    addNode('n1');
    addProvenanceEdges(graph, 'src1', ['n1', 'missing-node']);
    expect(graph.size).toBe(1); // Only the existing member gets an edge
  });

  it('does not add duplicate provenance edges on repeated calls', () => {
    const groupNodeId = `${SOURCE_GROUP_PREFIX}src1`;
    addNode(groupNodeId);
    addNode('n1');
    addProvenanceEdges(graph, 'src1', ['n1']);
    addProvenanceEdges(graph, 'src1', ['n1']);
    expect(graph.size).toBe(1);
  });

  it('provenance edge has correct sourceId and targetId attributes', () => {
    const groupNodeId = `${SOURCE_GROUP_PREFIX}src1`;
    addNode(groupNodeId);
    addNode('n1');
    addProvenanceEdges(graph, 'src1', ['n1']);
    const edgeId = `${SOURCE_PROVENANCE_PREFIX}src1:n1`;
    const attrs = graph.getEdgeAttributes(edgeId);
    expect(attrs.sourceId).toBe(groupNodeId);
    expect(attrs.targetId).toBe('n1');
  });

  it('handles empty member list gracefully', () => {
    const groupNodeId = `${SOURCE_GROUP_PREFIX}src1`;
    addNode(groupNodeId);
    expect(() => addProvenanceEdges(graph, 'src1', [])).not.toThrow();
    expect(graph.size).toBe(0);
  });
});

// ========================================
// Integration: full round-trip
// ========================================

describe('full round-trip: populateGraphFromApi + addSourceGroupNode + addProvenanceEdges', () => {
  it('correctly wires source groups into a populated graph', () => {
    const graph = new Graph<NodeAttributes, EdgeAttributes>();
    const nodes: ApiNode[] = [
      makeApiNode({ id: 'n1', template_id: 'tmpl-a', position: { x: 0, y: 0 } }),
      makeApiNode({ id: 'n2', template_id: 'tmpl-b', position: { x: 100, y: 100 } }),
    ];
    const edges: ApiEdge[] = [
      makeApiEdge({ id: 'e1', template_id: 'tmpl-e', source_node_id: 'n1', target_node_id: 'n2' }),
    ];
    populateGraphFromApi(graph, nodes, edges, undefined);

    const group = makeSourceGroup({
      source_id: 'src1',
      title: 'Doc',
      entity_node_ids: ['n1', 'n2'],
    });
    const memberIds = addSourceGroupNode(graph, group);
    addProvenanceEdges(graph, 'src1', memberIds);

    // Original nodes + 1 group node
    expect(graph.order).toBe(3);
    // Original edge + 2 provenance edges
    expect(graph.size).toBe(3);

    const groupNodeId = `${SOURCE_GROUP_PREFIX}src1`;
    expect(graph.hasNode(groupNodeId)).toBe(true);
    expect(graph.hasEdge(`${SOURCE_PROVENANCE_PREFIX}src1:n1`)).toBe(true);
    expect(graph.hasEdge(`${SOURCE_PROVENANCE_PREFIX}src1:n2`)).toBe(true);
  });

  it('applyDegreeSizing does not affect source group nodes after integration', () => {
    const graph = new Graph<NodeAttributes, EdgeAttributes>();
    const nodes: ApiNode[] = [
      makeApiNode({ id: 'n1', template_id: 'tmpl-a', position: { x: 0, y: 0 } }),
    ];
    populateGraphFromApi(graph, nodes, [], undefined);

    const group = makeSourceGroup({
      source_id: 'src1',
      entity_node_ids: ['n1'],
    });
    const memberIds = addSourceGroupNode(graph, group);
    addProvenanceEdges(graph, 'src1', memberIds);

    const groupNodeId = `${SOURCE_GROUP_PREFIX}src1`;
    const sizeBefore = graph.getNodeAttribute(groupNodeId, 'size');
    applyDegreeSizing(graph);
    const sizeAfter = graph.getNodeAttribute(groupNodeId, 'size');
    expect(sizeAfter).toBe(sizeBefore); // Should not change
  });
});
