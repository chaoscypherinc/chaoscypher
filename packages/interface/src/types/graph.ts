// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

// Graph domain type definitions for Chaos Cypher frontend
import type { PaginationMetadata } from '../services/crudApiFactory';

export interface PropertyDefinition {
  name: string;
  display_name: string;
  property_type: string;
  required?: boolean;
  default_value?: unknown;
  enum_values?: string[];
  description?: string;
  validation_pattern?: string;
  allowed_node_types?: string[];
}

export interface Template {
  id: string;
  name: string;
  description?: string;
  template_type: 'node' | 'edge';
  properties: PropertyDefinition[];
  is_system: boolean;
  icon?: string | null;
  color?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Node {
  id: string;
  template_id: string;
  title?: string; // Node title
  label?: string; // For backward compatibility
  type?: string; // Node type
  content?: Record<string, unknown>; // Node content as JSON
  properties?: Record<string, unknown>; // Additional properties
  tags?: string[]; // Node tags
  position?: { x: number; y: number }; // Position for graph canvas
  embedding?: number[];
  source_id?: string; // If this node is a source document
  created_at: string;
  updated_at: string;
  // Stats fields (populated when include_stats=true)
  edge_count?: number;
  incoming_edge_count?: number;
  outgoing_edge_count?: number;
  citation_count?: number;
  relationship_type_count?: number;
}

export interface ConnectedNode {
  id: string;
  label: string;
  template_id: string;
  edge_count: number;
  relationship: string;
  direction: 'incoming' | 'outgoing';
}

export interface ConnectionsResponse {
  data: ConnectedNode[];
  pagination: PaginationMetadata;
}

export interface SourceReference {
  id: string;
  title: string;
  source_type: string;
  origin_url?: string;
}

export interface ChunkReference {
  id: string;
  content: string;
  page_number?: number;
  section?: string;
  chunk_metadata?: Record<string, unknown>;
}

export interface Citation {
  id: string;
  source: SourceReference;
  chunk: ChunkReference;
  confidence: number;
  extraction_method: string;
  context_snippet?: string;
  citation_metadata?: { sent_ref?: string };
  created_at: string;
}

export interface CitationListResponse {
  data: Citation[];
  pagination: PaginationMetadata;
}

export interface Edge {
  id: string;
  template_id: string;
  source_node_id: string;
  target_node_id: string;
  label?: string;
  type?: string; // Edge type
  properties?: Record<string, unknown>;
  created_at: string;
  updated_at?: string;
}

export interface NodeCreateRequest {
  template_id: string;
  title?: string;
  label?: string;
  content?: Record<string, unknown>;
  properties?: Record<string, unknown>;
  tags?: string[];
  position?: { x: number; y: number };
  embedding?: number[];
}

export interface EdgeCreateRequest {
  template_id: string;
  source_node_id: string;
  target_node_id: string;
  label?: string;
  properties?: Record<string, unknown>;
}

// ========================================
// Bulk Canvas Data (Single-Request Graph Load)
// ========================================

/** Minimal node data for canvas rendering (from GET /api/v1/graph/canvas). */
export interface CanvasNode {
  id: string;
  template_id: string;
  label: string;
  position?: { x: number; y: number } | null;
  source_id?: string | null;
}

/** Minimal edge data for canvas rendering (from GET /api/v1/graph/canvas). */
export interface CanvasEdge {
  id: string;
  source_node_id: string;
  target_node_id: string;
  template_id: string;
  label: string;
}

/** Minimal template data for canvas rendering (from GET /api/v1/graph/canvas). */
export interface CanvasTemplate {
  id: string;
  name: string;
  template_type: string;
  icon?: string | null;
  color?: string | null;
  description?: string | null;
}

/** Response from GET /api/v1/graph/canvas. */
export interface CanvasDataResponse {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  templates: CanvasTemplate[];
  total_nodes: number;
  total_edges: number;
  /** True when the graph exceeds canvas rendering limits (100K nodes / 300K edges). */
  truncated: boolean;
}

// ========================================
// Source Groups (Graph Canvas Visualization)
// ========================================

/** Source group for graph canvas visualization. */
export interface SourceGroup {
  source_id: string;
  title: string;
  source_type: string;
  filename: string;
  extraction_domain?: string;
  extraction_domain_icon?: string;
  entity_count: number;
  entity_node_ids: string[];
}

/** Response from GET /api/v1/graph/source_groups. */
export interface SourceGroupListResponse {
  groups: SourceGroup[];
}
