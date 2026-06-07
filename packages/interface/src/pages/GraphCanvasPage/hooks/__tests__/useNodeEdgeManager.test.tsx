// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import type { Node as ApiNode, Edge as ApiEdge, Template } from '../../../../types';
import { useNodeEdgeManager } from '../useNodeEdgeManager';

// ---------------------------------------------------------------------------
// Mock: services/api
// ---------------------------------------------------------------------------

const mockNodeApiCreate = vi.fn<(payload: unknown) => Promise<ApiNode>>();
const mockNodeApiUpdate = vi.fn<(id: string, payload: unknown) => Promise<ApiNode>>();
const mockNodeApiDelete = vi.fn<(id: string) => Promise<void>>();

const mockEdgeApiCreate = vi.fn<(payload: unknown) => Promise<ApiEdge>>();
const mockEdgeApiDelete = vi.fn<(id: string) => Promise<void>>();

const mockTemplateApiGet = vi.fn<(id: string) => Promise<Template>>();

vi.mock('../../../../services/api/nodes', () => ({
  nodeApi: {
    create: (...args: Parameters<typeof mockNodeApiCreate>) => mockNodeApiCreate(...args),
    update: (...args: Parameters<typeof mockNodeApiUpdate>) => mockNodeApiUpdate(...args),
    delete: (...args: Parameters<typeof mockNodeApiDelete>) => mockNodeApiDelete(...args),
  },
}));

vi.mock('../../../../services/api/edges', () => ({
  edgeApi: {
    create: (...args: Parameters<typeof mockEdgeApiCreate>) => mockEdgeApiCreate(...args),
    delete: (...args: Parameters<typeof mockEdgeApiDelete>) => mockEdgeApiDelete(...args),
  },
}));

vi.mock('../../../../services/api/templates', () => ({
  templateApi: {
    get: (...args: Parameters<typeof mockTemplateApiGet>) => mockTemplateApiGet(...args),
  },
}));

// ---------------------------------------------------------------------------
// Mock: logger
// ---------------------------------------------------------------------------

vi.mock('../../../../utils/logger', () => ({
  logger: {
    error: vi.fn<(msg: string, err?: unknown) => void>(),
    info: vi.fn<(msg: string) => void>(),
    warn: vi.fn<(msg: string) => void>(),
  },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeGraph(): Graph<NodeAttributes, EdgeAttributes> {
  return new Graph<NodeAttributes, EdgeAttributes>();
}

function makeNodeAttrs(overrides: Partial<NodeAttributes> = {}): NodeAttributes {
  return {
    nodeId: 'n1',
    title: 'Test Node',
    content: {},
    templateId: 'tpl1',
    tags: [],
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    x: 100,
    y: 200,
    size: 8,
    color: '#00E5FF',
    label: 'Test Node',
    ...overrides,
  };
}

function makeApiNode(overrides: Partial<ApiNode> = {}): ApiNode {
  return {
    id: 'node-1',
    template_id: 'tpl1',
    label: 'New Node',
    title: 'New Node',
    content: {},
    properties: {},
    tags: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeApiEdge(overrides: Partial<ApiEdge> = {}): ApiEdge {
  return {
    id: 'edge-1',
    template_id: 'etpl1',
    source_node_id: 'src-node',
    target_node_id: 'tgt-node',
    label: 'relates to',
    properties: {},
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeTemplate(overrides: Partial<Template> = {}): Template {
  return {
    id: 'tpl1',
    name: 'Test Template',
    template_type: 'node',
    properties: [],
    is_system: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function renderManager(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  setError = vi.fn<(err: string | null) => void>(),
  setIsPropertiesPanelOpen = vi.fn<(open: boolean) => void>(),
) {
  return renderHook(() =>
    useNodeEdgeManager({ graph, setError, setIsPropertiesPanelOpen }),
  );
}

// ---------------------------------------------------------------------------

describe('useNodeEdgeManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // -------------------------------------------------------------------------
  // handleNodeCreate
  // -------------------------------------------------------------------------

  describe('handleNodeCreate', () => {
    it('calls templateApi.get then nodeApi.create, adds the node to graph', async () => {
      const graph = makeGraph();
      const template = makeTemplate();
      const apiNode = makeApiNode({ id: 'node-new' });

      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockResolvedValueOnce(apiNode);

      const { result } = renderManager(graph);

      let returnedId: string | undefined;
      await act(async () => {
        returnedId = await result.current.handleNodeCreate('tpl1', { x: 50, y: 75 });
      });

      expect(mockTemplateApiGet).toHaveBeenCalledWith('tpl1');
      expect(mockNodeApiCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          template_id: 'tpl1',
          label: 'New Node',
          position: { x: 50, y: 75 },
        }),
      );
      expect(graph.hasNode('node-new')).toBe(true);
      expect(returnedId).toBe('node-new');
    });

    it('creates node without position when position is omitted', async () => {
      const graph = makeGraph();
      const template = makeTemplate();
      const apiNode = makeApiNode({ id: 'node-no-pos' });

      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockResolvedValueOnce(apiNode);

      const { result } = renderManager(graph);

      await act(async () => {
        await result.current.handleNodeCreate('tpl1');
      });

      expect(mockNodeApiCreate).toHaveBeenCalledWith(
        expect.objectContaining({ position: undefined }),
      );
      expect(graph.hasNode('node-no-pos')).toBe(true);
    });

    it('populates required text properties as empty strings', async () => {
      const graph = makeGraph();
      const template = makeTemplate({
        properties: [
          { name: 'name', display_name: 'Name', property_type: 'text', required: true },
          { name: 'desc', display_name: 'Desc', property_type: 'string', required: true },
        ],
      });
      const apiNode = makeApiNode();

      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockResolvedValueOnce(apiNode);

      const { result } = renderManager(graph);

      await act(async () => {
        await result.current.handleNodeCreate('tpl1');
      });

      expect(mockNodeApiCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          properties: expect.objectContaining({ name: '', desc: '' }),
        }),
      );
    });

    it('populates required integer/float properties as 0', async () => {
      const graph = makeGraph();
      const template = makeTemplate({
        properties: [
          { name: 'count', display_name: 'Count', property_type: 'integer', required: true },
          { name: 'score', display_name: 'Score', property_type: 'float', required: true },
        ],
      });
      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockResolvedValueOnce(makeApiNode());

      const { result } = renderManager(graph);
      await act(async () => {
        await result.current.handleNodeCreate('tpl1');
      });

      expect(mockNodeApiCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          properties: expect.objectContaining({ count: 0, score: 0 }),
        }),
      );
    });

    it('populates required boolean properties as false', async () => {
      const graph = makeGraph();
      const template = makeTemplate({
        properties: [
          { name: 'active', display_name: 'Active', property_type: 'boolean', required: true },
        ],
      });
      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockResolvedValueOnce(makeApiNode());

      const { result } = renderManager(graph);
      await act(async () => {
        await result.current.handleNodeCreate('tpl1');
      });

      expect(mockNodeApiCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          properties: expect.objectContaining({ active: false }),
        }),
      );
    });

    it('populates required json properties as {}', async () => {
      const graph = makeGraph();
      const template = makeTemplate({
        properties: [
          { name: 'meta', display_name: 'Meta', property_type: 'json', required: true },
        ],
      });
      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockResolvedValueOnce(makeApiNode());

      const { result } = renderManager(graph);
      await act(async () => {
        await result.current.handleNodeCreate('tpl1');
      });

      expect(mockNodeApiCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          properties: expect.objectContaining({ meta: {} }),
        }),
      );
    });

    it('populates required node_reference_list properties as []', async () => {
      const graph = makeGraph();
      const template = makeTemplate({
        properties: [
          { name: 'refs', display_name: 'Refs', property_type: 'node_reference_list', required: true },
        ],
      });
      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockResolvedValueOnce(makeApiNode());

      const { result } = renderManager(graph);
      await act(async () => {
        await result.current.handleNodeCreate('tpl1');
      });

      expect(mockNodeApiCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          properties: expect.objectContaining({ refs: [] }),
        }),
      );
    });

    it('uses default_value for required properties that have one', async () => {
      const graph = makeGraph();
      const template = makeTemplate({
        properties: [
          {
            name: 'status',
            display_name: 'Status',
            property_type: 'text',
            required: true,
            default_value: 'active',
          },
        ],
      });
      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockResolvedValueOnce(makeApiNode());

      const { result } = renderManager(graph);
      await act(async () => {
        await result.current.handleNodeCreate('tpl1');
      });

      expect(mockNodeApiCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          properties: expect.objectContaining({ status: 'active' }),
        }),
      );
    });

    it('skips non-required properties', async () => {
      const graph = makeGraph();
      const template = makeTemplate({
        properties: [
          { name: 'optional', display_name: 'Optional', property_type: 'text', required: false },
        ],
      });
      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockResolvedValueOnce(makeApiNode());

      const { result } = renderManager(graph);
      await act(async () => {
        await result.current.handleNodeCreate('tpl1');
      });

      const callArg = mockNodeApiCreate.mock.calls[0][0] as { properties: Record<string, unknown> };
      expect(callArg.properties).not.toHaveProperty('optional');
    });

    it('sets setError and rethrows on templateApi failure', async () => {
      const graph = makeGraph();
      const setError = vi.fn<(err: string | null) => void>();
      const error = new Error('Template not found');
      mockTemplateApiGet.mockRejectedValueOnce(error);

      const { result } = renderManager(graph, setError);

      await expect(
        act(async () => {
          await result.current.handleNodeCreate('tpl1');
        }),
      ).rejects.toThrow('Template not found');

      expect(setError).toHaveBeenCalledWith(expect.any(String));
    });

    it('sets setError and rethrows on nodeApi.create failure', async () => {
      const graph = makeGraph();
      const setError = vi.fn<(err: string | null) => void>();
      const template = makeTemplate();
      const error = new Error('Create failed');

      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockRejectedValueOnce(error);

      const { result } = renderManager(graph, setError);

      await expect(
        act(async () => {
          await result.current.handleNodeCreate('tpl1');
        }),
      ).rejects.toThrow('Create failed');

      expect(setError).toHaveBeenCalledWith(expect.any(String));
    });
  });

  // -------------------------------------------------------------------------
  // handleNodeUpdate
  // -------------------------------------------------------------------------

  describe('handleNodeUpdate', () => {
    it('calls nodeApi.update and updates graph node attributes', async () => {
      const graph = makeGraph();
      graph.addNode('n1', makeNodeAttrs({ x: 100, y: 200 }));

      const updatedApiNode = makeApiNode({
        id: 'n1',
        title: 'Updated Title',
        template_id: 'tpl2',
        tags: ['tag1'],
        updated_at: '2026-06-01T00:00:00Z',
      });
      mockNodeApiUpdate.mockResolvedValueOnce(updatedApiNode);

      const { result } = renderManager(graph);

      await act(async () => {
        await result.current.handleNodeUpdate('n1', { label: 'Updated Title' });
      });

      expect(mockNodeApiUpdate).toHaveBeenCalledWith(
        'n1',
        expect.objectContaining({ label: 'Updated Title', position: { x: 100, y: 200 } }),
      );

      const attrs = graph.getNodeAttributes('n1');
      expect(attrs.title).toBe('Updated Title');
      expect(attrs.templateId).toBe('tpl2');
      expect(attrs.tags).toEqual(['tag1']);
    });

    it('preserves position from graph when update does not include position', async () => {
      const graph = makeGraph();
      graph.addNode('n1', makeNodeAttrs({ x: 300, y: 400 }));

      const updatedApiNode = makeApiNode({ id: 'n1', title: 'Title' });
      mockNodeApiUpdate.mockResolvedValueOnce(updatedApiNode);

      const { result } = renderManager(graph);

      await act(async () => {
        await result.current.handleNodeUpdate('n1', {});
      });

      expect(mockNodeApiUpdate).toHaveBeenCalledWith(
        'n1',
        expect.objectContaining({ position: { x: 300, y: 400 } }),
      );
    });

    it('uses provided position in update payload when given', async () => {
      const graph = makeGraph();
      graph.addNode('n1', makeNodeAttrs({ x: 100, y: 200 }));

      const updatedApiNode = makeApiNode({ id: 'n1' });
      mockNodeApiUpdate.mockResolvedValueOnce(updatedApiNode);

      const { result } = renderManager(graph);

      await act(async () => {
        await result.current.handleNodeUpdate('n1', { position: { x: 999, y: 888 } });
      });

      expect(mockNodeApiUpdate).toHaveBeenCalledWith(
        'n1',
        expect.objectContaining({ position: { x: 999, y: 888 } }),
      );
    });

    it('handles update when node is not in the graph (no-op on attributes)', async () => {
      const graph = makeGraph();
      const updatedApiNode = makeApiNode({ id: 'missing-node' });
      mockNodeApiUpdate.mockResolvedValueOnce(updatedApiNode);

      const { result } = renderManager(graph);

      await act(async () => {
        // Node 'missing-node' was never added to graph.
        await result.current.handleNodeUpdate('missing-node', { label: 'X' });
      });

      // API call still fires.
      expect(mockNodeApiUpdate).toHaveBeenCalledWith('missing-node', expect.any(Object));
      // Graph remains empty.
      expect(graph.order).toBe(0);
    });

    it('extracts sourceDocumentId from content when it is a string', async () => {
      const graph = makeGraph();
      graph.addNode('n1', makeNodeAttrs());

      const updatedApiNode = makeApiNode({
        id: 'n1',
        content: { source_document_id: 'doc-123', source_document_name: 'My Doc' },
      });
      mockNodeApiUpdate.mockResolvedValueOnce(updatedApiNode);

      const { result } = renderManager(graph);

      await act(async () => {
        await result.current.handleNodeUpdate('n1', {});
      });

      const attrs = graph.getNodeAttributes('n1');
      expect(attrs.sourceDocumentId).toBe('doc-123');
      expect(attrs.sourceDocumentName).toBe('My Doc');
    });

    it('sets setError on nodeApi.update failure', async () => {
      const graph = makeGraph();
      graph.addNode('n1', makeNodeAttrs());
      const setError = vi.fn<(err: string | null) => void>();
      mockNodeApiUpdate.mockRejectedValueOnce(new Error('Update failed'));

      const { result } = renderManager(graph, setError);

      await act(async () => {
        await result.current.handleNodeUpdate('n1', {});
      });

      expect(setError).toHaveBeenCalledWith(expect.any(String));
    });
  });

  // -------------------------------------------------------------------------
  // handleNodeDelete
  // -------------------------------------------------------------------------

  describe('handleNodeDelete', () => {
    it('calls nodeApi.delete, drops node from graph, and closes properties panel', async () => {
      const graph = makeGraph();
      graph.addNode('n1', makeNodeAttrs());
      const setIsPropertiesPanelOpen = vi.fn<(open: boolean) => void>();
      mockNodeApiDelete.mockResolvedValueOnce(undefined);

      const { result } = renderManager(graph, undefined, setIsPropertiesPanelOpen);

      await act(async () => {
        await result.current.handleNodeDelete('n1');
      });

      expect(mockNodeApiDelete).toHaveBeenCalledWith('n1');
      expect(graph.hasNode('n1')).toBe(false);
      expect(setIsPropertiesPanelOpen).toHaveBeenCalledWith(false);
    });

    it('deletes node and also drops its connected edges', async () => {
      const graph = makeGraph();
      graph.addNode('n1', makeNodeAttrs({ nodeId: 'n1' }));
      graph.addNode('n2', makeNodeAttrs({ nodeId: 'n2' }));
      graph.addEdgeWithKey('e1', 'n1', 'n2', {
        edgeId: 'e1',
        label: 'rel',
        templateId: 'etpl',
        sourceId: 'n1',
        targetId: 'n2',
        properties: {},
        createdAt: '2026-01-01T00:00:00Z',
        updatedAt: '2026-01-01T00:00:00Z',
      });
      mockNodeApiDelete.mockResolvedValueOnce(undefined);

      const { result } = renderManager(graph);

      await act(async () => {
        await result.current.handleNodeDelete('n1');
      });

      expect(graph.hasNode('n1')).toBe(false);
      expect(graph.hasEdge('e1')).toBe(false);
    });

    it('still closes panel when node is not in the graph', async () => {
      const graph = makeGraph();
      const setIsPropertiesPanelOpen = vi.fn<(open: boolean) => void>();
      mockNodeApiDelete.mockResolvedValueOnce(undefined);

      const { result } = renderManager(graph, undefined, setIsPropertiesPanelOpen);

      await act(async () => {
        await result.current.handleNodeDelete('ghost-node');
      });

      expect(mockNodeApiDelete).toHaveBeenCalledWith('ghost-node');
      expect(setIsPropertiesPanelOpen).toHaveBeenCalledWith(false);
    });

    it('sets setError on nodeApi.delete failure', async () => {
      const graph = makeGraph();
      graph.addNode('n1', makeNodeAttrs());
      const setError = vi.fn<(err: string | null) => void>();
      mockNodeApiDelete.mockRejectedValueOnce(new Error('Delete failed'));

      const { result } = renderManager(graph, setError);

      await act(async () => {
        await result.current.handleNodeDelete('n1');
      });

      expect(setError).toHaveBeenCalledWith(expect.any(String));
    });
  });

  // -------------------------------------------------------------------------
  // handleNodeDuplicate
  // -------------------------------------------------------------------------

  describe('handleNodeDuplicate', () => {
    it('calls templateApi.get and nodeApi.create with (Copy) suffix, adds to graph', async () => {
      const graph = makeGraph();
      graph.addNode('n1', makeNodeAttrs({ x: 10, y: 20 }));

      const template = makeTemplate();
      const newApiNode = makeApiNode({ id: 'n-copy', label: 'Test Node (Copy)' });

      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockResolvedValueOnce(newApiNode);

      const { result } = renderManager(graph);

      await act(async () => {
        await result.current.handleNodeDuplicate('n1', {
          nodeId: 'n1',
          title: 'Test Node',
          content: { key: 'val' },
          templateId: 'tpl1',
          tags: [],
          createdAt: '2026-01-01T00:00:00Z',
          updatedAt: '2026-01-01T00:00:00Z',
        });
      });

      expect(mockTemplateApiGet).toHaveBeenCalledWith('tpl1');
      expect(mockNodeApiCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          template_id: 'tpl1',
          label: 'Test Node (Copy)',
          properties: { key: 'val' },
          position: { x: 60, y: 70 }, // x + 50, y + 50
        }),
      );
      expect(graph.hasNode('n-copy')).toBe(true);
    });

    it('duplicates without position offset when source node is not in graph', async () => {
      const graph = makeGraph();
      // Node 'n1' is NOT added to graph.
      const template = makeTemplate();
      const newApiNode = makeApiNode({ id: 'n-copy2' });

      mockTemplateApiGet.mockResolvedValueOnce(template);
      mockNodeApiCreate.mockResolvedValueOnce(newApiNode);

      const { result } = renderManager(graph);

      await act(async () => {
        await result.current.handleNodeDuplicate('n1', {
          nodeId: 'n1',
          title: 'Test Node',
          content: {},
          templateId: 'tpl1',
          tags: [],
          createdAt: '2026-01-01T00:00:00Z',
          updatedAt: '2026-01-01T00:00:00Z',
        });
      });

      expect(mockNodeApiCreate).toHaveBeenCalledWith(
        expect.objectContaining({ position: undefined }),
      );
    });

    it('sets setError on failure during duplication', async () => {
      const graph = makeGraph();
      const setError = vi.fn<(err: string | null) => void>();
      mockTemplateApiGet.mockRejectedValueOnce(new Error('Template fetch failed'));

      const { result } = renderManager(graph, setError);

      await act(async () => {
        await result.current.handleNodeDuplicate('n1', {
          nodeId: 'n1',
          title: 'Test Node',
          content: {},
          templateId: 'tpl1',
          tags: [],
          createdAt: '2026-01-01T00:00:00Z',
          updatedAt: '2026-01-01T00:00:00Z',
        });
      });

      expect(setError).toHaveBeenCalledWith(expect.any(String));
    });
  });

  // -------------------------------------------------------------------------
  // handleEdgeCreate
  // -------------------------------------------------------------------------

  describe('handleEdgeCreate', () => {
    it('calls edgeApi.create and adds edge to graph', async () => {
      const graph = makeGraph();
      graph.addNode('src', makeNodeAttrs({ nodeId: 'src' }));
      graph.addNode('tgt', makeNodeAttrs({ nodeId: 'tgt' }));

      const newEdge = makeApiEdge({
        id: 'edge-new',
        source_node_id: 'src',
        target_node_id: 'tgt',
        template_id: 'etpl1',
        label: 'connects',
      });
      mockEdgeApiCreate.mockResolvedValueOnce(newEdge);

      const { result } = renderManager(graph);

      let returnedId: string | undefined;
      await act(async () => {
        returnedId = await result.current.handleEdgeCreate('src', 'tgt', 'etpl1', 'connects');
      });

      expect(mockEdgeApiCreate).toHaveBeenCalledWith({
        source_node_id: 'src',
        target_node_id: 'tgt',
        template_id: 'etpl1',
        label: 'connects',
        properties: {},
      });
      expect(graph.hasEdge('edge-new')).toBe(true);
      expect(returnedId).toBe('edge-new');
    });

    it('uses empty string label when none is provided', async () => {
      const graph = makeGraph();
      graph.addNode('src', makeNodeAttrs({ nodeId: 'src' }));
      graph.addNode('tgt', makeNodeAttrs({ nodeId: 'tgt' }));

      const newEdge = makeApiEdge({ id: 'edge-nolabel', source_node_id: 'src', target_node_id: 'tgt' });
      mockEdgeApiCreate.mockResolvedValueOnce(newEdge);

      const { result } = renderManager(graph);

      await act(async () => {
        await result.current.handleEdgeCreate('src', 'tgt', 'etpl1');
      });

      expect(mockEdgeApiCreate).toHaveBeenCalledWith(
        expect.objectContaining({ label: '' }),
      );
    });

    it('sets setError and rethrows on edgeApi.create failure', async () => {
      const graph = makeGraph();
      const setError = vi.fn<(err: string | null) => void>();
      mockEdgeApiCreate.mockRejectedValueOnce(new Error('Edge create failed'));

      const { result } = renderManager(graph, setError);

      await expect(
        act(async () => {
          await result.current.handleEdgeCreate('src', 'tgt', 'etpl1', 'label');
        }),
      ).rejects.toThrow('Edge create failed');

      expect(setError).toHaveBeenCalledWith(expect.any(String));
    });
  });

  // -------------------------------------------------------------------------
  // handleEdgeDelete
  // -------------------------------------------------------------------------

  describe('handleEdgeDelete', () => {
    it('calls edgeApi.delete, drops edge from graph, and closes properties panel', async () => {
      const graph = makeGraph();
      graph.addNode('src', makeNodeAttrs({ nodeId: 'src' }));
      graph.addNode('tgt', makeNodeAttrs({ nodeId: 'tgt' }));
      graph.addEdgeWithKey('e1', 'src', 'tgt', {
        edgeId: 'e1',
        label: 'rel',
        templateId: 'etpl',
        sourceId: 'src',
        targetId: 'tgt',
        properties: {},
        createdAt: '2026-01-01T00:00:00Z',
        updatedAt: '2026-01-01T00:00:00Z',
      });

      const setIsPropertiesPanelOpen = vi.fn<(open: boolean) => void>();
      mockEdgeApiDelete.mockResolvedValueOnce(undefined);

      const { result } = renderManager(graph, undefined, setIsPropertiesPanelOpen);

      await act(async () => {
        await result.current.handleEdgeDelete('e1');
      });

      expect(mockEdgeApiDelete).toHaveBeenCalledWith('e1');
      expect(graph.hasEdge('e1')).toBe(false);
      expect(setIsPropertiesPanelOpen).toHaveBeenCalledWith(false);
    });

    it('still closes panel when edge is not in the graph', async () => {
      const graph = makeGraph();
      const setIsPropertiesPanelOpen = vi.fn<(open: boolean) => void>();
      mockEdgeApiDelete.mockResolvedValueOnce(undefined);

      const { result } = renderManager(graph, undefined, setIsPropertiesPanelOpen);

      await act(async () => {
        await result.current.handleEdgeDelete('ghost-edge');
      });

      expect(mockEdgeApiDelete).toHaveBeenCalledWith('ghost-edge');
      expect(setIsPropertiesPanelOpen).toHaveBeenCalledWith(false);
    });

    it('sets setError on edgeApi.delete failure', async () => {
      const graph = makeGraph();
      const setError = vi.fn<(err: string | null) => void>();
      mockEdgeApiDelete.mockRejectedValueOnce(new Error('Edge delete failed'));

      const { result } = renderManager(graph, setError);

      await act(async () => {
        await result.current.handleEdgeDelete('e1');
      });

      expect(setError).toHaveBeenCalledWith(expect.any(String));
    });
  });

  // -------------------------------------------------------------------------
  // Error message surface: getApiErrorMessage
  // -------------------------------------------------------------------------

  describe('error message extraction', () => {
    it('uses response.data.message when present', async () => {
      const graph = makeGraph();
      const setError = vi.fn<(err: string | null) => void>();

      const httpError = Object.assign(new Error('http'), {
        response: { data: { message: 'API said: not found' } },
      });
      mockTemplateApiGet.mockRejectedValueOnce(httpError);

      const { result } = renderManager(graph, setError);

      await expect(
        act(async () => {
          await result.current.handleNodeCreate('tpl1');
        }),
      ).rejects.toThrow();

      expect(setError).toHaveBeenCalledWith('API said: not found');
    });

    it('falls back to Error.message when no response envelope', async () => {
      const graph = makeGraph();
      const setError = vi.fn<(err: string | null) => void>();

      mockNodeApiDelete.mockRejectedValueOnce(new Error('plain error message'));

      const { result } = renderManager(graph, setError);

      await act(async () => {
        await result.current.handleNodeDelete('n1');
      });

      expect(setError).toHaveBeenCalledWith('plain error message');
    });

    it('uses fallback message when getApiErrorMessage returns "Unknown error" (falsy-reject)', async () => {
      const graph = makeGraph();
      const setError = vi.fn<(err: string | null) => void>();

      // Throwing null: getApiErrorMessage(null) returns 'Unknown error'.
      // The hook does: setError(getApiErrorMessage(err) || 'Failed to delete item').
      // 'Unknown error' is truthy, so setError receives 'Unknown error'.
      mockNodeApiDelete.mockRejectedValueOnce(null);

      const { result } = renderManager(graph, setError);

      await act(async () => {
        await result.current.handleNodeDelete('n1');
      });

      expect(setError).toHaveBeenCalledWith('Unknown error');
    });
  });

  // -------------------------------------------------------------------------
  // Return shape
  // -------------------------------------------------------------------------

  describe('return shape', () => {
    it('returns all six handler functions', () => {
      const graph = makeGraph();
      const { result } = renderManager(graph);
      expect(typeof result.current.handleNodeCreate).toBe('function');
      expect(typeof result.current.handleNodeUpdate).toBe('function');
      expect(typeof result.current.handleNodeDelete).toBe('function');
      expect(typeof result.current.handleNodeDuplicate).toBe('function');
      expect(typeof result.current.handleEdgeCreate).toBe('function');
      expect(typeof result.current.handleEdgeDelete).toBe('function');
    });
  });
});
