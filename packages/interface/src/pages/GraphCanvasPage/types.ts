// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * GraphCanvasPage TypeScript types
 *
 * Clean application types for the knowledge graph canvas.
 * Used by Sigma.js/graphology rendering layer.
 */

/**
 * Data stored on each graphology node
 */
export interface GraphNodeData {
  nodeId: string;
  title: string;
  content: Record<string, unknown>;
  templateId: string;
  type?: string;
  /** The pack subtype (`properties.entity_subtype`); the template is the core type's. */
  subtype?: string;
  tags: string[];
  createdAt: string;
  updatedAt: string;
  sourceDocumentId?: string;
  sourceDocumentName?: string;
}

/**
 * Data stored on each graphology edge
 */
export interface GraphEdgeData {
  edgeId: string;
  label: string;
  templateId: string;
  sourceId: string;
  targetId: string;
  type?: string;
  properties: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

/**
 * Graphology node attributes (GraphNodeData + rendering attrs)
 */
export interface NodeAttributes extends GraphNodeData {
  x: number;
  y: number;
  size: number;
  color: string;
  label: string;
  image?: string | null;
  type?: string;
  hidden?: boolean;
  /** True if this is a virtual source group node. */
  isSourceGroup?: boolean;
  /** Source ID (for source group nodes). */
  sourceGroupId?: string;
  /** Entity count (for source group nodes). */
  sourceGroupEntityCount?: number;
  /** Source ID this entity was extracted from (set on member nodes). */
  sourceGroupMembership?: string;
}

/**
 * Graphology edge attributes (GraphEdgeData + rendering attrs)
 */
export interface EdgeAttributes extends GraphEdgeData {
  color?: string;
  size?: number;
  hidden?: boolean;
  /** True if this is a virtual provenance edge. */
  isProvenance?: boolean;
  /** Drawn dashed (a pipeline-flagged edge: ``utils/edgeFlags.ts``); set by the edge reducer. */
  dashed?: boolean;
}

/**
 * Layout algorithm types
 */
export type LayoutType = 'force' | 'grid' | 'mindmap' | 'hierarchical' | 'radial' | 'manual';

// ========================================
// Source Group Virtual Element Helpers
// ========================================

/** Prefix for source group virtual node IDs. */
export const SOURCE_GROUP_PREFIX = 'sg:';

/** Prefix for source provenance virtual edge IDs. */
export const SOURCE_PROVENANCE_PREFIX = 'sp:';

/** Check if a node ID is a virtual source group node. */
export function isSourceGroupNode(nodeId: string): boolean {
  return nodeId.startsWith(SOURCE_GROUP_PREFIX);
}

/** Check if an edge ID is a virtual provenance edge. */
function isProvenanceEdge(edgeId: string): boolean {
  return edgeId.startsWith(SOURCE_PROVENANCE_PREFIX);
}

// ========================================
// The Unresolved bucket (utils/unresolvedBucket.ts)
// ========================================

/** Prefix for the synthetic "Unresolved (N)" hub nodes, one per source document. */
const UNRESOLVED_BUCKET_PREFIX = 'ub:';

/** Prefix for the synthetic edges linking an Unresolved hub to its members. */
export const UNRESOLVED_EDGE_PREFIX = 'ue:';

/** Prefix for the synthetic edge linking a document root to its Unresolved hub. */
export const UNRESOLVED_ROOT_EDGE_PREFIX = 'ur:';

/**
 * Id of a source document's Unresolved hub; `null` for the one hub holding
 * the unresolved nodes with no known source.
 */
export function unresolvedBucketId(sourceId: string | null): string {
  return `${UNRESOLVED_BUCKET_PREFIX}${sourceId ?? ''}`;
}

/** The source document an Unresolved hub belongs to; `null` for the no-source hub. */
export function bucketSourceId(hubId: string): string | null {
  return hubId.slice(UNRESOLVED_BUCKET_PREFIX.length) || null;
}

/** Check if a node ID is a synthetic Unresolved hub (any source's). */
export function isUnresolvedBucketNode(nodeId: string): boolean {
  return nodeId.startsWith(UNRESOLVED_BUCKET_PREFIX);
}

// ========================================
// Synthetic elements: the one check
// ========================================

/**
 * True for a node the canvas made up: a source's root or the Unresolved hub.
 *
 * A synthetic node is drawn like any other but is not graph data: it is never
 * sent to the API, searched, filtered, laid out, sized by degree, edited,
 * duplicated or deleted. Every data path tests this, not a per-kind check.
 */
export function isSyntheticNode(nodeId: string): boolean {
  return isSourceGroupNode(nodeId) || isUnresolvedBucketNode(nodeId);
}

/**
 * True for an edge the canvas made up: a provenance edge, an Unresolved hub's
 * edge to a member, or a document root's edge to its Unresolved hub.
 */
export function isSyntheticEdge(edgeId: string): boolean {
  return (
    isProvenanceEdge(edgeId)
    || edgeId.startsWith(UNRESOLVED_EDGE_PREFIX)
    || edgeId.startsWith(UNRESOLVED_ROOT_EDGE_PREFIX)
  );
}
