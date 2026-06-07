// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TypeScript shapes for the graph snapshot API.
 *
 * TODO: Once the OpenAPI regeneration flow is stable, run `npm run generate-types`
 * and replace these hand-written interfaces with the generated equivalents from
 * `types/generated/api.ts`.
 *
 * These mirror `GraphBreakdown` and sub-models from:
 *   packages/core/src/chaoscypher_core/services/graph/snapshot/models.py
 */

export interface TemplateEntry {
  id: string;
  name: string;
  /** Hex colour string (e.g. "#ff0000"). Backend falls back to "#888888" for NULL. */
  color: string;
  count: number;
}

export interface SourceBreakdown {
  id: string;
  name: string;
  /** Free-form source type string (e.g. "pdf", "text", "url"). */
  source_type: string;
  total_entities: number;
  total_internal_links: number;
  /** Sorted by count descending. */
  templates: TemplateEntry[];
}

export interface GraphStats {
  total_nodes: number;
  total_edges: number;
  total_sources: number;
}

export interface GraphBreakdown {
  /** Schema version — currently 2. */
  version: number;
  /** ISO-8601 UTC timestamp when the snapshot was generated. */
  generated_at: string;
  database_name: string;
  title: string | null;
  stats: GraphStats;
  /** Sorted by total_entities descending. */
  sources: SourceBreakdown[];
}
