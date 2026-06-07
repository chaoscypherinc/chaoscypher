// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Source domain types for the Chaos Cypher frontend.
 *
 * `Source` and `SourceSummary` are re-exported from the OpenAPI-generated
 * schema at `packages/interface/src/types/generated/api.ts`.
 * The canonical definitions live in
 * `packages/cortex/src/chaoscypher_cortex/features/sources/models.py`.
 * To refresh after backend changes: `make types`.
 *
 * - `Source`        — full detail (GET /api/v1/sources/{id}, ~70 fields)
 * - `SourceSummary` — list projection (GET /api/v1/sources, ~43 fields,
 *                     excludes large JSON/BLOB columns for list-view performance)
 */

import type { components } from './generated/api';
import type { PaginationMetadata } from '../services/crudApiFactory';

type StageProgressRecord = components['schemas']['StageProgressRecord'];

/** Full source detail (GET /api/v1/sources/{id}). */
export type Source = components['schemas']['SourceResponse'];

/** List-view projection (GET /api/v1/sources). Excludes large payload fields. */
export type SourceSummary = components['schemas']['SourceSummaryResponse'];

// ========================================
// Domain confirmation gate types
// ========================================

/** One candidate domain from the detection ranking (highest score first). */
export interface RankedDomain {
  domain: string;
  score: number;
}

// ========================================
// Unified Source Type (combines processing + active)
// ========================================

export interface UnifiedSource {
  id: string;
  stage: 'queued' | 'processing' | 'active';
  title: string;
  source_type: string;
  size: number;
  status: string;
  created_at: string;
  tags?: { id: string; name: string; color?: string }[];

  // Domain (available for committed/active sources)
  extraction_domain?: string;
  extraction_domain_auto?: boolean;
  extraction_domain_icon?: string;

  // Domain confirmation gate (awaiting_confirmation sources). Present only
  // for parked sources; sourced from the backend detection proposal blob.
  // `detection_ranking[0]` is the winner; an empty ranking + low_confidence
  // means "detection wasn't confident — pick a domain".
  confirmation_required?: boolean;
  extraction_confirmed_at?: string | null;
  detection_ranking?: RankedDomain[];
  detection_confidence?: number;
  detection_low_confidence?: boolean;
  proposed_extraction_options?: {
    analysis_depth?: 'quick' | 'full';
    domain?: string;
    filtering_mode?: string;
    content_filtering?: boolean;
    enable_direction_correction?: boolean;
    protect_orphans?: boolean;
    enable_inverse_relationships?: boolean;
    max_entity_degree_override?: number | null;
    // The eager-detection proposal blob (set verbatim by the backend) also
    // carries the heuristic's verdict fields. `no_text` is true for the
    // image-only / <50-char short-circuit, letting the confirm UI show
    // "not enough text to detect — pick a domain" instead of the generic
    // low-confidence prompt.
    low_confidence?: boolean;
    no_text?: boolean;
  };

  // Recovery state
  error_stage?: string;
  recovery_attempts?: number;

  // Vector-search visibility (Workstream 10).  Surfaced flat from
  // SourceSummaryResponse so the source list can render
  // SearchStatusBadge without loading the full QualityMetrics object.
  vector_indexing_status?: string;
  vector_indexed_at?: string | null;

  // Per-source pause state
  is_paused?: boolean;
  paused_at?: string | null;
  paused_reason?: string | null;

  // Queued/Processing-specific (null for active)
  ingestion?: {
    extraction_depth?: string;
    current_step?: number;
    total_steps?: number;
    step_description?: string;
    duration_seconds?: number;
    error_message?: string;
    analysis_id?: string;
    // Indexing stats
    chunks_count?: number;
    embedding_model?: string;
    embedding_dimensions?: number;
    indexing_started_at?: string;
    // Extraction stats
    entities_count?: number;
    relationships_count?: number;
    extraction_started_at?: string;
    // MCP extraction progress
    extraction_mode?: string | null;
    // Stage-level progress (LLM stage progress facility)
    stage_progress?: Record<string, StageProgressRecord>;
    // Commit stats
    nodes_created?: number;
    edges_created?: number;
    templates_created?: number;
  };

  // Active-specific (null for queued/processing)
  active?: {
    chunk_count: number;
    enabled: boolean;
    embedding_model?: string;
    embedding_dimensions?: number;
    // Import stats (from commit process)
    entities_count?: number;
    relationships_count?: number;
    nodes_created?: number;
    edges_created?: number;
    templates_created?: number;
    // Processing durations (seconds)
    indexing_duration_seconds?: number;
    extraction_duration_seconds?: number;
    commit_duration_seconds?: number;
    // LLM Metrics for success indicator and tooltip
    llm_total_calls?: number;
    llm_first_try_successes?: number;
    llm_retry_successes?: number;
    llm_permanent_failures?: number;
    llm_total_input_tokens?: number;
    llm_total_output_tokens?: number;
    llm_model?: string;
    extraction_mode?: string;
  };
}

export interface ExtractedEntity {
  id?: string;
  type: string;
  name: string;
  context?: string;
  description?: string;
  aliases?: string[];
  properties?: Record<string, unknown>;
  confidence?: number;
  quality_score?: number;
  chunk_index?: number;
}

export interface InferredRelationship {
  source?: number;             // Index-based: source entity index
  target?: number;             // Index-based: target entity index
  from?: string;               // Name-based: source entity name
  to?: string;                 // Name-based: target entity name
  type: string;
  template_id?: string;
  confidence: number;
  justification?: string;
  properties?: Record<string, unknown>;
}

// ========================================
// Sources System Types (Unified Model)
// ========================================

/**
 * Helper to check if source is in a processing state.
 * Works with both Source (detail) and SourceSummary (list) since status is present in both.
 */
export const isSourceProcessing = (source: Source | SourceSummary): boolean => {
  return ['pending', 'indexing', 'vision_pending', 'extracting', 'mcp_extracting', 'committing'].includes(source.status);
};

/**
 * Helper to check if source has completed indexing (chunks available).
 * Works with both Source (detail) and SourceSummary (list).
 */
export const isSourceIndexed = (source: Source | SourceSummary): boolean => {
  return ['indexed', 'extracting', 'mcp_extracting', 'extracted', 'committing', 'committed'].includes(source.status);
};

/**
 * Helper to check if source has completed extraction (entities available).
 * Works with both Source (detail) and SourceSummary (list).
 */
export const isSourceExtracted = (source: Source | SourceSummary): boolean => {
  return ['extracted', 'committing', 'committed'].includes(source.status);
};

/**
 * Helper to check if source is committed to graph.
 * Works with both Source (detail) and SourceSummary (list).
 */
export const isSourceCommitted = (source: Source | SourceSummary): boolean => {
  return source.status === 'committed';
};

/**
 * Statuses where the dedicated Re-extract action (audit fix #F49) is
 * meaningful. Mirrors the cortex `reextract_source` service contract:
 * any status that has produced (or is producing) extraction artifacts
 * — plus ERROR, since errored sources may have partial artifacts to
 * discard. PENDING/INDEXING are excluded because no extraction has
 * happened yet and a normal triggerExtraction is the right path.
 *
 * Single source of truth for both action menus (SourcePage header and
 * the Sources list row menu); keep in sync with the backend whitelist
 * in `packages/cortex/src/chaoscypher_cortex/features/sources/service.py`
 * (`reextract_source`).
 */
export const REEXTRACTABLE_STATUSES: ReadonlySet<string> = new Set([
  'indexed',
  'extracted',
  'extracting',
  'mcp_extracting',
  'committing',
  'committed',
  'error',
]);

export interface SourceUpdate {
  title?: string;
  processing_status?: string;
  enabled?: boolean;
  user_metadata?: Record<string, unknown>;
}

export interface PaginatedSourcesResponse {
  data: SourceSummary[];
  pagination: {
    total: number;
    page: number;
    page_size: number;
    total_pages: number;
    has_next: boolean;
    has_prev: boolean;
  };
}

export interface SourceTag {
  id: string;
  database_name: string;
  name: string;
  color?: string;
  description?: string;
  created_at: string;
}

export interface SourceTagCreate {
  name: string;
  color?: string;
  description?: string;
}

export interface SourceTagUpdate {
  name?: string;
  color?: string;
  description?: string;
}

export interface SourceChunk {
  id: string;
  source_id?: string;
  chunk_index: number;
  content: string;
  page_number?: number;
  section?: string;
  group_index?: number;
  status: 'staged' | 'committed';
  created_at: string;
}

export interface SourceChunkListResponse {
  data: SourceChunk[];
  pagination: PaginationMetadata;
}

// ========================================
// Extraction Task Types (LLM Processing)
// ========================================

export interface ExtractionTask {
  id: string;
  job_id: string;
  chunk_index: number;
  hierarchical_group_id?: string;
  small_chunk_ids?: string[];
  small_chunk_numbers?: number[];  // 1-indexed chunk numbers for UI display
  status: 'pending' | 'queued' | 'running' | 'completed' | 'failed';

  // Timing
  created_at: string;
  queued_at?: string;
  started_at?: string;
  completed_at?: string;
  llm_duration_ms?: number;

  // Results
  retry_count: number;
  entity_count: number;
  relationship_count: number;
  invalid_relationship_count: number;

  // Input/Output lengths (for charts without loading full text)
  input_text_length?: number;
  llm_response_length?: number;

  // Token tracking (actual counts from LLM API)
  input_tokens?: number;
  output_tokens?: number;
  context_window_available?: number;

  // Input/Output content (optional, for detail views)
  input_text?: string;
  llm_response_json?: string;

  // Pipeline filtering diagnostics (optional, for detail views)
  filtering_log?: FilteringLog;

  // Error info
  error_message?: string;
  error_type?: string;
}

export interface ExtractionTaskListResponse {
  tasks: ExtractionTask[];
  total: number;
  page: number;
  page_size: number;
}

export interface ExtractionTaskStats {
  total_tasks: number;
  context_window?: number;
  // Input tokens
  min_input_tokens?: number;
  max_input_tokens?: number;
  avg_input_tokens?: number;
  // Output tokens
  min_output_tokens?: number;
  max_output_tokens?: number;
  avg_output_tokens?: number;
  // Total tokens (input + output)
  min_total_tokens?: number;
  max_total_tokens?: number;
  avg_total_tokens?: number;
  // Utilization percentages
  min_utilization?: number;
  max_utilization?: number;
  avg_utilization?: number;
  // Duration
  min_duration_ms?: number;
  max_duration_ms?: number;
  avg_duration_ms?: number;
  // Entity counts
  total_entities: number;
  avg_entities_per_task: number;
  // Relationship counts
  total_relationships: number;
  avg_relationships_per_task: number;
  // Retry stats
  total_retries: number;
  max_retries_single_task: number;
  // Invalid relationship stats
  total_invalid_relationships: number;
  avg_invalid_per_task: number;
  // Pipeline filtering aggregates
  total_entities_filtered: number;
  total_relationships_filtered: number;
  filtering_stage_summary?: FilteringStageSummary[];

  // Shared LLM prompts (from job, same for all chunks)
  system_prompt?: string;
  // Pass-1 entity prompt template (chunk text shown as a placeholder).
  user_instructions?: string;
  // Pass-2 relationship prompt template (chunk text + pass-1 entities shown
  // as placeholders). Absent on sources extracted before 2026-05-26.
  relationship_instructions?: string;
  // Separate parts for distinct UI display
  user_instructions_template?: string;
  extraction_rules_template?: string;
  entity_templates?: string;
  relationship_templates?: string;
  domain_guidance?: string;
  domain_examples?: string;
}

/**
 * Minimal task data for chart rendering (all tasks, no content).
 */
export interface ExtractionChartTask {
  id: string;
  chunk_index: number;
  status: string;
  retry_count: number;
  entity_count: number;
  relationship_count: number;
  invalid_relationship_count: number;
  input_text_length?: number;
  llm_duration_ms?: number;
}

// ========================================
// Pipeline Filtering Types
// ========================================

export interface FilteredItem {
  item_type: 'entity' | 'relationship';
  name: string;
  entity_type: string;
  reason: string;
  details?: Record<string, unknown>;
}

export interface FilterStageResult {
  stage: string;
  input_count: number;
  removed_count: number;
  items: FilteredItem[];
}

export interface FilteringLog {
  version: number;
  total_removed: number;
  stages: FilterStageResult[];
}

export interface FilteringStageSummary {
  stage: string;
  total_removed: number;
  chunk_count: number;
}

export interface SourceStats {
  total_chunks: number;
  committed_chunks: number;
  staged_chunks: number;
  total_citations: number;
  total_content_length: number;
  entity_count: number;
  relationship_count: number;
  entity_type_distribution: Record<string, number>;
  relationship_type_distribution: Record<string, number>;
  top_entities: Array<{ label: string; type: string | null; count: number }>;
  avg_confidence: number;
}
