// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Data Transformers: Convert between API format and graphology graph
 */

import type Graph from 'graphology';
import type { Node as ApiNode, Edge as ApiEdge, Template } from '../../../types';
import type { SourceGroup } from '../../../types/graph';
import type { NodeAttributes, EdgeAttributes } from '../types';
import { SOURCE_GROUP_PREFIX, SOURCE_PROVENANCE_PREFIX, isSyntheticNode } from '../types';
import { getColorForTemplate } from '../../../utils/colorUtils';
import { getIconDataUri, DEFAULT_NODE_ICON } from '../../../utils/iconSprites';
import { entitySubtype } from '../../../utils/entityTypes';

const DEFAULT_NODE_SIZE = 8;

/**
 * Lookup template icon/color from a template map. A node's template is its
 * core type's (the commit creates one per core type), so the colour is the
 * core type's; the pack subtype rides on the node (`subtype`) for its label.
 */
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
      subtype: entitySubtype(content),
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
    subtype: entitySubtype(content),
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

  graph.forEachNode((node) => {
    // Synthetic hubs (source roots, the Unresolved hub) keep their hub size
    if (isSyntheticNode(node)) return;

    const degree = graph.degree(node);
    // Logarithmic interpolation: 0 connections → MIN_SIZE, max connections → MAX_SIZE
    const t = Math.log2(degree + 1) / Math.log2(maxDegree + 1);
    const size = Math.round(MIN_SIZE + t * (MAX_SIZE - MIN_SIZE));
    graph.setNodeAttribute(node, 'size', size);
  });
}

// ========================================
// Synthetic hubs: shared builders
// ========================================

/** Size of a synthetic hub node (a source's root); the Unresolved hub is half of it. */
export const HUB_NODE_SIZE = 12;

/** The Unresolved hub is drawn at half a source root's size, so it never pulls focus. */
export const UNRESOLVED_HUB_SIZE = HUB_NODE_SIZE / 2;
import { ChaosCypherPalette, ChaosCypherNeutrals } from '../../../theme/palette';

/** Base colour of a hub edge (the edge reducer redraws it faint). */
const HUB_EDGE_COLOR = ChaosCypherNeutrals.textSecondary;

/** What a synthetic hub node shows. */
export interface HubNodeSpec {
  id: string;
  title: string;
  label: string;
  /** Marks the hub kind; never a real template. */
  templateId: string;
  color: string;
  /** Icon sprite (``getIconDataUri``); drawn as a pictogram when present. */
  image?: string | null;
  /** Node size; a source root's `HUB_NODE_SIZE` when absent. */
  size?: number;
  x: number;
  y: number;
}

/**
 * Attributes of a synthetic hub node: hub-sized, glowing in its colour, with
 * no content. The one shape every hub (source root, Unresolved) is built from.
 */
export function hubNodeAttributes(spec: HubNodeSpec): NodeAttributes {
  return {
    nodeId: spec.id,
    title: spec.title,
    label: spec.label,
    content: {},
    templateId: spec.templateId,
    tags: [],
    createdAt: '',
    updatedAt: '',
    x: spec.x,
    y: spec.y,
    size: spec.size ?? HUB_NODE_SIZE,
    color: spec.color,
    borderColor: spec.color,
    borderSize: 0.3,
    image: spec.image ?? undefined,
    type: spec.image ? 'pictogram' : undefined,
    hidden: false,
  } as NodeAttributes;
}

/** Centroid of the given nodes' positions; null when none of them is in the graph. */
export function centroidOf(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  nodeIds: readonly string[],
): { x: number; y: number } | null {
  let sumX = 0;
  let sumY = 0;
  let count = 0;
  for (const nodeId of nodeIds) {
    if (!graph.hasNode(nodeId)) continue;
    const attrs = graph.getNodeAttributes(nodeId);
    sumX += attrs.x || 0;
    sumY += attrs.y || 0;
    count += 1;
  }
  return count === 0 ? null : { x: sumX / count, y: sumY / count };
}

/**
 * Add one synthetic edge from a hub to a member, styled faint by the edge
 * reducer. Skips an existing or parallel edge. The one way a hub is linked.
 *
 * @param extra - Kind-specific attributes (template marker, provenance flag).
 */
export function addHubEdge(
  graph: Graph<NodeAttributes, EdgeAttributes>,
  edgeId: string,
  hubId: string,
  nodeId: string,
  extra: Partial<EdgeAttributes> = {},
): void {
  if (!graph.hasNode(hubId) || !graph.hasNode(nodeId) || graph.hasEdge(edgeId)) return;
  try {
    graph.addEdgeWithKey(edgeId, hubId, nodeId, {
      edgeId,
      label: '',
      templateId: '',
      sourceId: hubId,
      targetId: nodeId,
      properties: {},
      createdAt: '',
      updatedAt: '',
      color: HUB_EDGE_COLOR,
      size: 1,
      hidden: false,
      ...extra,
    });
  } catch {
    // Parallel edge — skip silently
  }
}

// ========================================
// Virtual Source Group Nodes & Edges
// ========================================

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

  // Centroid of member entities (all present, so never null)
  const center = centroidOf(graph, presentNodeIds) ?? { x: 0, y: 0 };

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
      ...hubNodeAttributes({
        id: groupNodeId,
        title: group.title,
        label: `${group.title} (${presentNodeIds.length})`,
        templateId: '__source_group__',
        color: groupColor,
        image,
        x: center.x,
        y: center.y,
      }),
      isSourceGroup: true,
      sourceGroupId: group.source_id,
      sourceGroupEntityCount: presentNodeIds.length,
    });
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
    addHubEdge(graph, `${SOURCE_PROVENANCE_PREFIX}${sourceId}:${nodeId}`, groupNodeId, nodeId, {
      templateId: '__provenance__',
      isProvenance: true,
    });
  }
}
