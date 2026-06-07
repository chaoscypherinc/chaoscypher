// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Data Transformers: Convert between API format and graphology graph
 */

import type Graph from 'graphology';
import type { Node as ApiNode, Edge as ApiEdge, Template } from '../../../types';
import type { SourceGroup } from '../../../types/graph';
import type { NodeAttributes, EdgeAttributes } from '../types';
import { SOURCE_GROUP_PREFIX, SOURCE_PROVENANCE_PREFIX } from '../types';
import { getColorForTemplate } from '../../../utils/colorUtils';
import { getIconDataUri, DEFAULT_NODE_ICON } from '../../../utils/iconSprites';

const DEFAULT_NODE_SIZE = 8;

/** Lookup template icon/color from a template map. */
function resolveNodeVisuals(
  templateId: string,
  templateMap?: Map<string, Template>,
): { color: string; image?: string; nodeType?: string } {
  const template = templateMap?.get(templateId);
  const color = template?.color || getColorForTemplate(templateId);

  const iconName = template?.icon || DEFAULT_NODE_ICON;
  const image = getIconDataUri(iconName);
  if (image) {
    return { color, image, nodeType: 'pictogram' };
  }

  return { color };
}

/**
 * Populate a graphology graph from API data (bulk load).
 * Clears the graph first.
 */
export function populateGraphFromApi(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  apiNodes: ApiNode[],
  apiEdges: ApiEdge[],
  templateMap?: Map<string, Template>,
): void {
  graph.clear();

  for (const node of apiNodes) {
    const title = node.title || node.label || 'Untitled';
    const templateId = node.template_id || 'default';
    const content = (node.content || node.properties || {}) as Record<string, unknown>;
    const visuals = resolveNodeVisuals(templateId, templateMap);

    const attrs: NodeAttributes = {
      nodeId: node.id,
      title,
      content,
      templateId,
      type: visuals.nodeType || node.type,
      tags: node.tags || [],
      createdAt: node.created_at,
      updatedAt: node.updated_at,
      sourceDocumentId:
        node.source_id
        || (typeof content.source_document_id === 'string' ? content.source_document_id : undefined),
      sourceDocumentName:
        typeof content.source_document_name === 'string'
          ? content.source_document_name
          : undefined,
      x: node.position?.x || Math.random() * 1000,
      y: node.position?.y || Math.random() * 800,
      size: DEFAULT_NODE_SIZE,
      color: visuals.color,
      label: title,
      image: visuals.image,
    };

    graph.addNode(node.id, attrs);
  }

  for (const edge of apiEdges) {
    // Only add edge if both endpoints exist
    if (!graph.hasNode(edge.source_node_id) || !graph.hasNode(edge.target_node_id)) {
      continue;
    }

    const edgeTemplate = templateMap?.get(edge.template_id);
    const edgeColor = edgeTemplate?.color
      || (edge.template_id ? getColorForTemplate(edge.template_id) : undefined);

    const attrs: EdgeAttributes = {
      edgeId: edge.id,
      label: edge.label || '',
      templateId: edge.template_id,
      sourceId: edge.source_node_id,
      targetId: edge.target_node_id,
      type: 'line',
      properties: edge.properties || {},
      createdAt: edge.created_at,
      updatedAt: edge.updated_at || edge.created_at,
      color: edgeColor,
      size: 1,
    };

    // Skip duplicate edges (same key or same source→target pair in non-multi graph)
    if (graph.hasEdge(edge.id)) continue;

    try {
      graph.addEdgeWithKey(edge.id, edge.source_node_id, edge.target_node_id, attrs);
    } catch {
      // Parallel edge between same nodes — skip silently
    }
  }
}

/**
 * Add a single API node to an existing graph.
 */
export function addApiNodeToGraph(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  apiNode: ApiNode,
  position?: { x: number; y: number },
  templateMap?: Map<string, Template>,
): void {
  const title = apiNode.title || apiNode.label || 'Untitled';
  const templateId = apiNode.template_id || 'default';
  const content = (apiNode.content || apiNode.properties || {}) as Record<string, unknown>;
  const visuals = resolveNodeVisuals(templateId, templateMap);

  const attrs: NodeAttributes = {
    nodeId: apiNode.id,
    title,
    content,
    templateId,
    type: visuals.nodeType || apiNode.type,
    tags: apiNode.tags || [],
    createdAt: apiNode.created_at,
    updatedAt: apiNode.updated_at,
    sourceDocumentId:
      typeof content.source_document_id === 'string' ? content.source_document_id : undefined,
    sourceDocumentName:
      typeof content.source_document_name === 'string'
        ? content.source_document_name
        : undefined,
    x: position?.x ?? apiNode.position?.x ?? Math.random() * 1000,
    y: position?.y ?? apiNode.position?.y ?? Math.random() * 800,
    size: DEFAULT_NODE_SIZE,
    color: visuals.color,
    label: title,
    image: visuals.image,
  };

  if (!graph.hasNode(apiNode.id)) {
    graph.addNode(apiNode.id, attrs);
  }
}

/**
 * Add a single API edge to an existing graph.
 */
export function addApiEdgeToGraph(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  apiEdge: ApiEdge,
  templateMap?: Map<string, Template>,
): void {
  if (!graph.hasNode(apiEdge.source_node_id) || !graph.hasNode(apiEdge.target_node_id)) {
    return;
  }
  if (graph.hasEdge(apiEdge.id)) {
    return;
  }

  const edgeTemplate = templateMap?.get(apiEdge.template_id);
  const edgeColor = edgeTemplate?.color
    || (apiEdge.template_id ? getColorForTemplate(apiEdge.template_id) : undefined);

  const attrs: EdgeAttributes = {
    edgeId: apiEdge.id,
    label: apiEdge.label || '',
    templateId: apiEdge.template_id,
    sourceId: apiEdge.source_node_id,
    targetId: apiEdge.target_node_id,
    type: apiEdge.type,
    properties: apiEdge.properties || {},
    createdAt: apiEdge.created_at,
    updatedAt: apiEdge.updated_at || apiEdge.created_at,
    color: edgeColor,
    size: 2,
  };

  graph.addEdgeWithKey(apiEdge.id, apiEdge.source_node_id, apiEdge.target_node_id, attrs);
}

/**
 * Scale node sizes based on connection count (degree).
 * Hub nodes keep the original size (8); peripheral nodes with fewer
 * connections shrink down so clusters feel tighter and hubs stand out.
 * Call after populateGraphFromApi once all nodes and edges are loaded.
 */
export function applyDegreeSizing(
  graph: Graph<NodeAttributes, EdgeAttributes>,
): void {
  const MAX_SIZE = 8;  // Original default — hubs stay this size
  const MIN_SIZE = 3;

  // Find the highest degree in the graph for normalization
  let maxDegree = 1;
  graph.forEachNode((node) => {
    const deg = graph.degree(node);
    if (deg > maxDegree) maxDegree = deg;
  });

  graph.forEachNode((node, attrs) => {
    // Source group nodes have their own fixed size
    if (attrs.isSourceGroup) return;

    const degree = graph.degree(node);
    // Logarithmic interpolation: 0 connections → MIN_SIZE, max connections → MAX_SIZE
    const t = Math.log2(degree + 1) / Math.log2(maxDegree + 1);
    const size = Math.round(MIN_SIZE + t * (MAX_SIZE - MIN_SIZE));
    graph.setNodeAttribute(node, 'size', size);
  });
}

// ========================================
// Virtual Source Group Nodes & Edges
// ========================================

const SOURCE_GROUP_SIZE = 12;
import { ChaosCypherPalette, ChaosCypherNeutrals } from '../../../theme/palette';

const PROVENANCE_EDGE_COLOR = ChaosCypherNeutrals.textSecondary;
const SOURCE_DEFAULT_COLOR = ChaosCypherPalette.primary;

/** Source group nodes always use gold — they serve a different role than
 *  template-colored nodes and need to stand out as focal anchors. */
function getSourceGroupColor(_group: SourceGroup): string {
  return SOURCE_DEFAULT_COLOR;
}

/**
 * Add a virtual source group node at the centroid of its member entities.
 * Sets sourceGroupMembership on each member node.
 * Returns the list of member node IDs that actually exist in the graph.
 */
export function addSourceGroupNode(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  group: SourceGroup,
): string[] {
  const groupNodeId = `${SOURCE_GROUP_PREFIX}${group.source_id}`;

  // Find which member entities actually exist in the graph
  const presentNodeIds = group.entity_node_ids.filter((id) => graph.hasNode(id));
  if (presentNodeIds.length === 0) return [];

  // Calculate centroid of member entities
  let sumX = 0;
  let sumY = 0;
  for (const nodeId of presentNodeIds) {
    const attrs = graph.getNodeAttributes(nodeId);
    sumX += attrs.x || 0;
    sumY += attrs.y || 0;
  }
  const cx = sumX / presentNodeIds.length;
  const cy = sumY / presentNodeIds.length;

  // Mark member nodes
  for (const nodeId of presentNodeIds) {
    graph.setNodeAttribute(nodeId, 'sourceGroupMembership', group.source_id);
  }

  // Resolve domain icon for the source group node
  const iconName = group.extraction_domain_icon;
  const image = iconName ? getIconDataUri(iconName) : undefined;
  const groupColor = getSourceGroupColor(group);

  // Add virtual group node
  if (!graph.hasNode(groupNodeId)) {
    graph.addNode(groupNodeId, {
      nodeId: groupNodeId,
      title: group.title,
      label: `${group.title} (${presentNodeIds.length})`,
      content: {},
      templateId: '__source_group__',
      tags: [],
      createdAt: '',
      updatedAt: '',
      x: cx,
      y: cy,
      size: SOURCE_GROUP_SIZE,
      color: groupColor,
      borderColor: groupColor,
      borderSize: 0.3,
      image,
      type: image ? 'pictogram' : undefined,
      isSourceGroup: true,
      sourceGroupId: group.source_id,
      sourceGroupEntityCount: presentNodeIds.length,
      hidden: false,
    } as NodeAttributes);
  }

  return presentNodeIds;
}

/**
 * Add virtual provenance edges from a source group node to its member entities.
 */
export function addProvenanceEdges(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  sourceId: string,
  memberNodeIds: string[],
): void {
  const groupNodeId = `${SOURCE_GROUP_PREFIX}${sourceId}`;
  if (!graph.hasNode(groupNodeId)) return;

  for (const nodeId of memberNodeIds) {
    if (!graph.hasNode(nodeId)) continue;
    const edgeId = `${SOURCE_PROVENANCE_PREFIX}${sourceId}:${nodeId}`;
    if (graph.hasEdge(edgeId)) continue;

    try {
      graph.addEdgeWithKey(edgeId, groupNodeId, nodeId, {
        edgeId,
        label: '',
        templateId: '__provenance__',
        sourceId: groupNodeId,
        targetId: nodeId,
        properties: {},
        createdAt: '',
        updatedAt: '',
        color: PROVENANCE_EDGE_COLOR,
        size: 1,
        isProvenance: true,
        hidden: false,
      } as EdgeAttributes);
    } catch {
      // Parallel edge — skip silently
    }
  }
}

