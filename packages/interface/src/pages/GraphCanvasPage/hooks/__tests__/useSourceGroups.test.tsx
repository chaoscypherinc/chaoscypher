// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import { SOURCE_GROUP_PREFIX, SOURCE_PROVENANCE_PREFIX } from '../../types';
import { useSourceGroups } from '../useSourceGroups';
import type { SourceGroup } from '../../../../types/graph';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../../../../services/api/graph', () => ({
  graphApi: {
    fetchSourceGroups: vi.fn<() => Promise<{ groups: SourceGroup[] }>>(),
    fetchCanvasData: vi.fn<() => Promise<unknown>>(),
  },
}));

vi.mock('../../../../utils/logger', () => ({
  logger: {
    error: vi.fn<(msg: string, err: unknown) => void>(),
    info: vi.fn<(msg: string) => void>(),
    warn: vi.fn<(msg: string) => void>(),
  },
}));

// Import the mocked modules to access the mock fns
import { graphApi } from '../../../../services/api/graph';
import { logger } from '../../../../utils/logger';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function nodeAttrs(overrides: Partial<NodeAttributes> = {}): NodeAttributes {
  return {
    nodeId: 'n',
    title: 'Node',
    content: {},
    templateId: 't1',
    tags: [],
    createdAt: '2026-01-01',
    updatedAt: '2026-01-01',
    x: 10,
    y: 20,
    size: 5,
    color: '#00E5FF',
    label: 'Node',
    ...overrides,
  };
}

function makeGraph(): Graph<NodeAttributes, EdgeAttributes> {
  return new Graph<NodeAttributes, EdgeAttributes>();
}

/** Build a graph with member nodes that belong to a source. */
function buildGraphWithMembers(memberIds: string[]): Graph<NodeAttributes, EdgeAttributes> {
  const graph = makeGraph();
  memberIds.forEach((id, idx) => {
    graph.addNode(id, nodeAttrs({ nodeId: id, label: id, x: idx * 10, y: 0 }));
  });
  return graph;
}

function makeSourceGroup(overrides: Partial<SourceGroup> = {}): SourceGroup {
  return {
    source_id: 'src-1',
    title: 'My Source',
    source_type: 'pdf',
    filename: 'my-doc.pdf',
    entity_count: 2,
    entity_node_ids: ['node-a', 'node-b'],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useSourceGroups', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // -------------------------------------------------------------------------
  // Initial state
  // -------------------------------------------------------------------------

  describe('initial state', () => {
    it('starts with empty groups map, not loading, empty collapsed sets', () => {
      const { result } = renderHook(() => useSourceGroups());
      expect(result.current.groups.size).toBe(0);
      expect(result.current.loading).toBe(false);
      expect(result.current.collapsedMemberIds.size).toBe(0);
      expect(result.current.collapsedSourceIds.size).toBe(0);
    });
  });

  // -------------------------------------------------------------------------
  // loadSourceGroups — happy path
  // -------------------------------------------------------------------------

  describe('loadSourceGroups', () => {
    it('sets loading=true during fetch then false after', async () => {
      const group = makeSourceGroup();
      let resolvePromise!: (val: { groups: SourceGroup[] }) => void;
      const pending = new Promise<{ groups: SourceGroup[] }>((res) => { resolvePromise = res; });
      vi.mocked(graphApi.fetchSourceGroups).mockReturnValueOnce(pending);

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      act(() => {
        void result.current.loadSourceGroups(graph);
      });

      // Loading should be true while pending
      expect(result.current.loading).toBe(true);

      await act(async () => {
        resolvePromise({ groups: [group] });
        await pending;
      });

      await waitFor(() => expect(result.current.loading).toBe(false));
    });

    it('populates groups map with source group state', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      expect(result.current.groups.size).toBe(1);
      const state = result.current.groups.get('src-1');
      expect(state).toBeDefined();
      expect(state?.group.source_id).toBe('src-1');
      expect(state?.group.title).toBe('My Source');
      expect(state?.memberNodeIds).toContain('node-a');
      expect(state?.memberNodeIds).toContain('node-b');
      expect(state?.expanded).toBe(true);
    });

    it('adds a SOURCE_GROUP_PREFIX node to the graph', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      const groupNodeId = `${SOURCE_GROUP_PREFIX}src-1`;
      expect(graph.hasNode(groupNodeId)).toBe(true);
      const attrs = graph.getNodeAttributes(groupNodeId);
      expect(attrs.isSourceGroup).toBe(true);
      expect(attrs.sourceGroupId).toBe('src-1');
    });

    it('adds provenance edges from group node to member nodes', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      const edgeIdA = `${SOURCE_PROVENANCE_PREFIX}src-1:node-a`;
      const edgeIdB = `${SOURCE_PROVENANCE_PREFIX}src-1:node-b`;
      expect(graph.hasEdge(edgeIdA)).toBe(true);
      expect(graph.hasEdge(edgeIdB)).toBe(true);

      const edgeAttrsA = graph.getEdgeAttributes(edgeIdA);
      expect(edgeAttrsA.isProvenance).toBe(true);
    });

    it('skips groups where no entity nodes exist in the graph', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-x', 'node-y'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      // Graph has none of the member nodes
      const graph = makeGraph();
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      expect(result.current.groups.size).toBe(0);
    });

    it('handles multiple source groups', async () => {
      const groupA = makeSourceGroup({
        source_id: 'src-a',
        title: 'Source A',
        entity_node_ids: ['node-a'],
      });
      const groupB = makeSourceGroup({
        source_id: 'src-b',
        title: 'Source B',
        entity_node_ids: ['node-b'],
      });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [groupA, groupB] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      expect(result.current.groups.size).toBe(2);
      expect(result.current.groups.has('src-a')).toBe(true);
      expect(result.current.groups.has('src-b')).toBe(true);
    });

    it('detects external node IDs (nodes with neighbors outside the group)', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      // Add an outside node connected to node-a
      graph.addNode('outside', nodeAttrs({ nodeId: 'outside', label: 'outside' }));
      graph.addEdgeWithKey('edge-out', 'node-a', 'outside', {
        edgeId: 'edge-out',
        label: '',
        templateId: 't1',
        sourceId: 'node-a',
        targetId: 'outside',
        properties: {},
        createdAt: '2026-01-01',
        updatedAt: '2026-01-01',
      });

      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      const state = result.current.groups.get('src-1');
      // node-a has a connection outside the group → should be in externalNodeIds
      expect(state?.externalNodeIds.has('node-a')).toBe(true);
      // node-b only connected inside the group or via provenance → not external
      expect(state?.externalNodeIds.has('node-b')).toBe(false);
    });

    it('only includes member nodes that actually exist in the graph', async () => {
      const group = makeSourceGroup({
        entity_node_ids: ['node-a', 'node-missing'],
      });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a']); // node-missing is absent
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      const state = result.current.groups.get('src-1');
      expect(state?.memberNodeIds).toContain('node-a');
      expect(state?.memberNodeIds).not.toContain('node-missing');
    });
  });

  // -------------------------------------------------------------------------
  // loadSourceGroups — error path
  // -------------------------------------------------------------------------

  describe('loadSourceGroups error handling', () => {
    it('logs error and sets loading=false when API throws', async () => {
      const apiError = new Error('Network failure');
      vi.mocked(graphApi.fetchSourceGroups).mockRejectedValueOnce(apiError);

      const graph = makeGraph();
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      expect(result.current.loading).toBe(false);
      expect(vi.mocked(logger.error)).toHaveBeenCalledWith(
        'Failed to load source groups:',
        apiError,
      );
    });

    it('leaves groups empty on error', async () => {
      vi.mocked(graphApi.fetchSourceGroups).mockRejectedValueOnce(new Error('oops'));

      const graph = makeGraph();
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      expect(result.current.groups.size).toBe(0);
    });
  });

  // -------------------------------------------------------------------------
  // toggleGroup
  // -------------------------------------------------------------------------

  describe('toggleGroup', () => {
    async function renderWithOneGroup() {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      return { result, graph };
    }

    it('collapses an expanded group and updates the group node label with count', async () => {
      const { result, graph } = await renderWithOneGroup();

      expect(result.current.groups.get('src-1')?.expanded).toBe(true);

      act(() => {
        result.current.toggleGroup(graph, 'src-1');
      });

      expect(result.current.groups.get('src-1')?.expanded).toBe(false);

      const groupNodeId = `${SOURCE_GROUP_PREFIX}src-1`;
      const label = graph.getNodeAttribute(groupNodeId, 'label');
      expect(label).toContain('My Source');
      expect(label).toContain('2'); // memberNodeIds.length
    });

    it('re-expands a collapsed group and restores the original title label', async () => {
      const { result, graph } = await renderWithOneGroup();

      // Collapse first
      act(() => {
        result.current.toggleGroup(graph, 'src-1');
      });

      // Expand again
      act(() => {
        result.current.toggleGroup(graph, 'src-1');
      });

      expect(result.current.groups.get('src-1')?.expanded).toBe(true);

      const groupNodeId = `${SOURCE_GROUP_PREFIX}src-1`;
      const label = graph.getNodeAttribute(groupNodeId, 'label');
      expect(label).toBe('My Source');
    });

    it('is a no-op for unknown sourceId', async () => {
      const { result, graph } = await renderWithOneGroup();
      const beforeSize = result.current.groups.size;

      act(() => {
        result.current.toggleGroup(graph, 'nonexistent-src');
      });

      expect(result.current.groups.size).toBe(beforeSize);
    });

    it('does not throw when group node is absent from the graph', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      // Remove the group node from the graph manually
      const groupNodeId = `${SOURCE_GROUP_PREFIX}src-1`;
      graph.dropNode(groupNodeId);

      expect(() => {
        act(() => {
          result.current.toggleGroup(graph, 'src-1');
        });
      }).not.toThrow();
    });
  });

  // -------------------------------------------------------------------------
  // expandAll / collapseAll
  // -------------------------------------------------------------------------

  describe('expandAll and collapseAll', () => {
    async function renderWithTwoGroups() {
      const groupA = makeSourceGroup({ source_id: 'src-a', title: 'A', entity_node_ids: ['na1', 'na2'] });
      const groupB = makeSourceGroup({ source_id: 'src-b', title: 'B', entity_node_ids: ['nb1', 'nb2'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [groupA, groupB] });

      const graph = buildGraphWithMembers(['na1', 'na2', 'nb1', 'nb2']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      return { result, graph };
    }

    it('collapseAll collapses all expanded groups', async () => {
      const { result, graph } = await renderWithTwoGroups();

      act(() => {
        result.current.collapseAll(graph);
      });

      expect(result.current.groups.get('src-a')?.expanded).toBe(false);
      expect(result.current.groups.get('src-b')?.expanded).toBe(false);
    });

    it('collapseAll updates group node labels with member count', async () => {
      const { result, graph } = await renderWithTwoGroups();

      act(() => {
        result.current.collapseAll(graph);
      });

      const labelA = graph.getNodeAttribute(`${SOURCE_GROUP_PREFIX}src-a`, 'label');
      const labelB = graph.getNodeAttribute(`${SOURCE_GROUP_PREFIX}src-b`, 'label');

      expect(labelA).toContain('A');
      expect(labelA).toContain('2');
      expect(labelB).toContain('B');
      expect(labelB).toContain('2');
    });

    it('expandAll expands all collapsed groups', async () => {
      const { result, graph } = await renderWithTwoGroups();

      // Collapse first
      act(() => {
        result.current.collapseAll(graph);
      });

      // Now expand all
      act(() => {
        result.current.expandAll(graph);
      });

      expect(result.current.groups.get('src-a')?.expanded).toBe(true);
      expect(result.current.groups.get('src-b')?.expanded).toBe(true);
    });

    it('expandAll restores group node labels to original titles', async () => {
      const { result, graph } = await renderWithTwoGroups();

      act(() => {
        result.current.collapseAll(graph);
      });

      act(() => {
        result.current.expandAll(graph);
      });

      const labelA = graph.getNodeAttribute(`${SOURCE_GROUP_PREFIX}src-a`, 'label');
      const labelB = graph.getNodeAttribute(`${SOURCE_GROUP_PREFIX}src-b`, 'label');

      expect(labelA).toBe('A');
      expect(labelB).toBe('B');
    });

    it('collapseAll is idempotent when all groups are already collapsed', async () => {
      const { result, graph } = await renderWithTwoGroups();

      act(() => { result.current.collapseAll(graph); });
      act(() => { result.current.collapseAll(graph); }); // second call should be no-op

      expect(result.current.groups.get('src-a')?.expanded).toBe(false);
      expect(result.current.groups.get('src-b')?.expanded).toBe(false);
    });

    it('expandAll is idempotent when all groups are already expanded', async () => {
      const { result, graph } = await renderWithTwoGroups();

      act(() => { result.current.expandAll(graph); });
      act(() => { result.current.expandAll(graph); }); // second call should be no-op

      expect(result.current.groups.get('src-a')?.expanded).toBe(true);
      expect(result.current.groups.get('src-b')?.expanded).toBe(true);
    });
  });

  // -------------------------------------------------------------------------
  // collapsedMemberIds and collapsedSourceIds (derived state)
  // -------------------------------------------------------------------------

  describe('collapsedMemberIds and collapsedSourceIds', () => {
    it('collapsedMemberIds is empty when all groups are expanded', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      expect(result.current.collapsedMemberIds.size).toBe(0);
    });

    it('collapsedMemberIds contains member IDs when group is collapsed', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      act(() => {
        result.current.toggleGroup(graph, 'src-1');
      });

      expect(result.current.collapsedMemberIds.has('node-a')).toBe(true);
      expect(result.current.collapsedMemberIds.has('node-b')).toBe(true);
    });

    it('collapsedSourceIds contains source IDs of collapsed groups', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      act(() => {
        result.current.toggleGroup(graph, 'src-1');
      });

      expect(result.current.collapsedSourceIds.has('src-1')).toBe(true);
    });

    it('collapsedSourceIds is empty when all groups are expanded', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      expect(result.current.collapsedSourceIds.size).toBe(0);
    });
  });

  // -------------------------------------------------------------------------
  // getNodeSourceGroup
  // -------------------------------------------------------------------------

  describe('getNodeSourceGroup', () => {
    it('returns the group state for a member node', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      const found = result.current.getNodeSourceGroup('node-a');
      expect(found).toBeDefined();
      expect(found?.group.source_id).toBe('src-1');
    });

    it('returns undefined for a node not in any group', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      const notFound = result.current.getNodeSourceGroup('node-unknown');
      expect(notFound).toBeUndefined();
    });

    it('returns undefined when groups is empty', () => {
      const { result } = renderHook(() => useSourceGroups());

      const notFound = result.current.getNodeSourceGroup('any-node');
      expect(notFound).toBeUndefined();
    });
  });

  // -------------------------------------------------------------------------
  // Membership marking on graph nodes
  // -------------------------------------------------------------------------

  describe('sourceGroupMembership on graph nodes', () => {
    it('sets sourceGroupMembership attribute on member nodes', async () => {
      const group = makeSourceGroup({ entity_node_ids: ['node-a', 'node-b'] });
      vi.mocked(graphApi.fetchSourceGroups).mockResolvedValueOnce({ groups: [group] });

      const graph = buildGraphWithMembers(['node-a', 'node-b']);
      const { result } = renderHook(() => useSourceGroups());

      await act(async () => {
        await result.current.loadSourceGroups(graph);
      });

      expect(graph.getNodeAttribute('node-a', 'sourceGroupMembership')).toBe('src-1');
      expect(graph.getNodeAttribute('node-b', 'sourceGroupMembership')).toBe('src-1');
    });
  });
});
