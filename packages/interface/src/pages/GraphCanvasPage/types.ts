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
export function isProvenanceEdge(edgeId: string): boolean {
  return edgeId.startsWith(SOURCE_PROVENANCE_PREFIX);
}
