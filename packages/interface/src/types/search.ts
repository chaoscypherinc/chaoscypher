// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

// Search domain type definitions for Chaos Cypher frontend

/**
 * Projection of a graph node as it appears in search results.
 *
 * Mirrors the backend `SearchNodeHit` DTO from
 * `packages/cortex/src/chaoscypher_cortex/features/search/models.py` — a
 * narrower shape than the full `Node` returned by `GET /nodes/{id}`. Only
 * the fields the search endpoint actually populates are declared here:
 *
 * - `id`, `label` — always populated by the engine.
 * - `template_id` — populated from the node's template, nullable because
 *   nodes without a template can still surface in results.
 *
 * Fields like `title`, `type` are never populated server-side and are
 * intentionally omitted. `edge_count` is populated by a batched query in
 * the search service so the omnibar can show real connection counts.
 */
export interface SearchNodeHit {
  id: string;
  label: string;
  template_id: string | null;
  edge_count?: number;
}

export interface ChunkResult {
  chunk_id: string;
  source_id: string;
  chunk_index: number;
  content: string;
  page_number?: number;
  section?: string;
  filename: string;
}

export interface SearchResult {
  node?: SearchNodeHit;
  chunk?: ChunkResult;
  score: number;
  result_type: 'node' | 'chunk';
}
