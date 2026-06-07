// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { DEFAULT_PUBLIC_SETTINGS } from '../../contexts/publicSettingsContextValue';
import { apiClient } from './client';
import { logger } from '../../utils/logger';
import type { PaginationMetadata } from '../crudApiFactory';
import type {
  Source,
  SourceSummary,
  PaginatedSourcesResponse,
  SourceChunk,
  SourceChunkListResponse,
  SourceStats,
  UnifiedSource,
  ExtractionTask,
  ExtractionTaskListResponse,
  ExtractionTaskStats,
  ExtractionChartTask,
  FilteringLog,
  ExtractedEntity,
  InferredRelationship,
} from '../../types';

/**
 * Status payload returned by GET /sources/{id}/extraction.
 *
 * Carries live extraction progress (timing for in-flight extractions) plus
 * any of the loosely-shaped status fields the backend exposes — additional
 * keys are tolerated via the index signature.
 */
interface SourceExtractionStatus {
  timing?: {
    estimated_remaining_seconds: number | null;
    elapsed_seconds: number | null;
    avg_chunk_time_seconds: number | null;
  } | null;
  [key: string]: unknown;
}

/**
 * Template summary returned by the source extraction endpoints. Mirrors the
 * shape consumed by SourcePage's ExtractionTab/OverviewTab — keep in sync if
 * the backend response changes.
 */
interface SourceTemplateSummary {
  id: string;
  name: string;
  description: string | null;
  template_type: string;
  properties: Array<{
    name: string;
    display_name?: string;
    property_type?: string;
    required?: boolean;
  }>;
  is_system: boolean;
  icon?: string | null;
  color?: string | null;
  source_id: string | null;
  node_count: number;
  edge_count: number;
  created_at: string;
  updated_at: string;
}

// Domain type for extraction domain selection
export interface ExtractionDomain {
  name: string;
  description: string;
  builtin: boolean;
  icon?: string;                // MUI icon name (e.g., "MenuBook"), null = fallback
  extraction_density?: number;  // Domain-specific output multiplier (default 1.0)
  prompt_tokens?: number;       // Estimated prompt tokens for this domain
}

/**
 * Confirmation override payload. Mirrors the backend ConfirmExtractionRequest
 * (which itself mirrors TriggerExtractionRequest, extraction_api.py:128-140).
 * All fields optional — omitted fields fall back to the proposal / domain
 * defaults server-side.
 */
export interface ConfirmExtractionOptions {
  domain?: string;
  analysis_depth?: 'quick' | 'full';
  filtering_mode?: string;
  content_filtering?: boolean;
  enable_direction_correction?: boolean;
  protect_orphans?: boolean;
  enable_inverse_relationships?: boolean;
  max_entity_degree_override?: number | null;
}

/**
 * Map a SourceSummary from the list API to a UnifiedSource for the UI.
 *
 * Uses SourceSummary (the list-view projection) rather than the full SourceResponse
 * because the list endpoint returns GET /api/v1/sources → SourceSummaryResponse[].
 * Fields only present in the full detail response (indexing_started_at,
 * extraction_started_at) are not available here and are omitted.
 */
function mapSourceToUnified(s: SourceSummary): UnifiedSource {
  const isActive = s.status === 'committed';
  const isProcessing = ['indexing', 'vision_pending', 'extracting', 'mcp_extracting', 'committing'].includes(s.status);
  const stage: 'queued' | 'processing' | 'active' = isActive
    ? 'active'
    : isProcessing
      ? 'processing'
      : 'queued';

  const base: UnifiedSource = {
    id: s.id,
    stage,
    title: s.title ?? s.filename ?? '',
    source_type: s.source_type ?? s.file_type ?? '',
    size: s.file_size ?? 0,
    status: s.status,
    created_at: s.created_at,
    tags: s.tags?.map(t => ({ id: t.id, name: t.name, color: t.color ?? undefined })),
    extraction_domain: s.extraction_domain ?? undefined,
    extraction_domain_auto: s.extraction_domain_auto,
    extraction_domain_icon: s.extraction_domain_icon ?? undefined,
    // Per-source pause state
    is_paused: s.is_paused ?? false,
    paused_at: s.paused_at,
    paused_reason: s.paused_reason,
    // Vector-search visibility (Workstream 10).
    vector_indexing_status: s.vector_indexing_status,
    vector_indexed_at: s.vector_indexed_at,
    // Domain confirmation gate. SourceSummaryResponse now carries these fields
    // (Phase 4 backend + types regen). extraction_confirmed_at maps with ?? null
    // to distinguish "confirmed" (datetime) from "never confirmed" (null) —
    // using ?? undefined would collapse both to the same absent value.
    // detection_ranking is typed as {[key:string]:unknown}[] by openapi-typescript
    // (the Python type is list[dict[str,Any]]); cast to RankedDomain[] since the
    // runtime shape is always {domain:string, score:number}.
    confirmation_required: s.confirmation_required ?? undefined,
    extraction_confirmed_at: s.extraction_confirmed_at ?? null,
    detection_ranking: s.detection_ranking as import('../../types/source').RankedDomain[] | undefined ?? undefined,
    detection_confidence: s.detection_confidence ?? undefined,
    detection_low_confidence: s.detection_low_confidence ?? undefined,
    proposed_extraction_options: s.proposed_extraction_options as UnifiedSource['proposed_extraction_options'] ?? undefined,
  };

  if (isActive) {
    base.active = {
      chunk_count: s.chunk_count,
      enabled: s.enabled,
      embedding_model: s.embedding_model ?? undefined,
      embedding_dimensions: s.embedding_dimensions ?? undefined,
      entities_count: s.extraction_entities_count,
      relationships_count: s.extraction_relationships_count,
      nodes_created: s.commit_nodes_created,
      edges_created: s.commit_edges_created,
      templates_created: s.commit_templates_created,
      indexing_duration_seconds: s.indexing_duration_seconds ?? undefined,
      extraction_duration_seconds: s.extraction_duration_seconds ?? undefined,
      commit_duration_seconds: s.commit_duration_seconds ?? undefined,
      llm_total_calls: s.llm_total_calls,
      llm_first_try_successes: s.llm_first_try_successes,
      llm_retry_successes: s.llm_retry_successes,
      llm_permanent_failures: s.llm_permanent_failures,
      llm_total_input_tokens: s.llm_total_input_tokens,
      llm_total_output_tokens: s.llm_total_output_tokens,
      llm_model: s.llm_model ?? undefined,
      extraction_mode: s.extraction_mode ?? undefined,
    };
  } else {
    base.ingestion = {
      extraction_depth: s.extraction_depth ?? undefined,
      current_step: s.current_step ?? undefined,
      total_steps: s.total_steps ?? undefined,
      step_description: s.step_description ?? undefined,
      duration_seconds: (s.indexing_duration_seconds || 0) + (s.extraction_duration_seconds || 0),
      error_message: s.error_message ?? undefined,
      // analysis_id is not present in SourceSummaryResponse (list projection)
      chunks_count: s.chunk_count || 0,
      embedding_model: s.embedding_model ?? undefined,
      embedding_dimensions: s.embedding_dimensions ?? undefined,
      // indexing_started_at / extraction_started_at are only in full SourceResponse
      entities_count: s.extraction_entities_count || 0,
      relationships_count: s.extraction_relationships_count || 0,
      // MCP extraction progress
      extraction_mode: s.extraction_mode ?? undefined,
      // Stage-level progress (LLM stage progress facility)
      stage_progress: s.stage_progress ?? {},
      nodes_created: s.commit_nodes_created || 0,
      edges_created: s.commit_edges_created || 0,
      templates_created: s.commit_templates_created || 0,
    };
  }

  return base;
}

export const sourceProcessingApi = {
  // ========================================
  // Chunk Operations
  // ========================================

  getChunks: async (
    sourceId: string,
    params?: {
      page?: number;
      page_size?: number;
      status?: string;
    }
  ): Promise<SourceChunkListResponse> => {
    const response = await apiClient.get<SourceChunkListResponse>(
      `/sources/${sourceId}/chunks`,
      { params }
    );
    return response.data;
  },

  getChunk: async (sourceId: string, chunkId: string): Promise<SourceChunk> => {
    const response = await apiClient.get<SourceChunk>(`/sources/${sourceId}/chunks/${chunkId}`);
    return response.data;
  },

  // ========================================
  // Extraction Task Operations (LLM Processing)
  // ========================================

  getExtractionTasks: async (
    sourceId: string,
    params?: {
      page?: number;
      page_size?: number;
      include_content?: boolean;
    }
  ): Promise<ExtractionTaskListResponse> => {
    const response = await apiClient.get<ExtractionTaskListResponse>(
      `/sources/${sourceId}/extraction/tasks`,
      { params }
    );
    return response.data;
  },

  getExtractionTask: async (sourceId: string, taskId: string): Promise<ExtractionTask> => {
    const response = await apiClient.get<ExtractionTask>(
      `/sources/${sourceId}/extraction/tasks/${taskId}`
    );
    return response.data;
  },

  getExtractionTaskStats: async (sourceId: string): Promise<ExtractionTaskStats> => {
    const response = await apiClient.get<ExtractionTaskStats>(
      `/sources/${sourceId}/extraction/stats`
    );
    return response.data;
  },

  getExtractionTasksForCharts: async (sourceId: string): Promise<ExtractionChartTask[]> => {
    const response = await apiClient.get<ExtractionChartTask[]>(
      `/sources/${sourceId}/extraction/charts`
    );
    return response.data;
  },

  getExtractionFilteringLog: async (sourceId: string): Promise<FilteringLog | null> => {
    try {
      const response = await apiClient.get<FilteringLog>(
        `/sources/${sourceId}/extraction/filteringlog`
      );
      return response.data;
    } catch {
      return null;
    }
  },

  // ========================================
  // Statistics
  // ========================================

  getStats: async (sourceId: string): Promise<SourceStats> => {
    const response = await apiClient.get<SourceStats>(`/sources/${sourceId}/stats`);
    return response.data;
  },

  // ========================================
  // Processing Operations (unified API - RESTful)
  // ========================================

  listDomains: async (): Promise<ExtractionDomain[]> => {
    const response = await apiClient.get<{ domains: ExtractionDomain[] }>('/sources/domains');
    return response.data.domains;
  },

  importUrl: async (
    url: string,
    extractEntities: boolean = true,
    analysisDepth: 'quick' | 'full' = 'full',
    enableNormalization: boolean = false,
    domain?: string,
    filteringMode?: string,
    contentFiltering: boolean = true,
    skipDuplicates: boolean = false,
  ): Promise<Source> => {
    const response = await apiClient.post<Source>('/sources/url', {
      url,
      extract_entities: extractEntities,
      analysis_depth: analysisDepth,
      enable_normalization: enableNormalization,
      domain: domain || null,
      filtering_mode: filteringMode || null,
      content_filtering: contentFiltering,
      skip_duplicates: skipDuplicates,
    });
    return response.data;
  },

  upload: async (
    file: File,
    extractEntities: boolean = true,
    analysisDepth: 'quick' | 'full' = 'full',
    enableNormalization: boolean = false,
    domain?: string,
    onUploadProgress?: (progress: number) => void,
    enableVision?: boolean | null,
    filteringMode?: string,
    signal?: AbortSignal,
    contentFiltering: boolean = true,
    skipDuplicates: boolean = false,
    autoConfirm: boolean = false,
  ): Promise<Source> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('extract_entities', extractEntities.toString());
    formData.append('analysis_depth', analysisDepth);
    formData.append('enable_normalization', enableNormalization.toString());
    formData.append('content_filtering', contentFiltering.toString());
    if (domain) {
      formData.append('domain', domain);
    }
    if (enableVision !== undefined && enableVision !== null) {
      formData.append('enable_vision', enableVision.toString());
    }
    if (filteringMode) {
      formData.append('filtering_mode', filteringMode);
    }
    if (skipDuplicates) {
      formData.append('skip_duplicates', 'true');
    }
    // Override fast-path: when the user pre-selects a specific domain in the
    // wizard's step 1, the upload carries auto_confirm=true so the backend
    // domain-confirmation gate is bypassed entirely (no park, no review).
    if (autoConfirm) {
      formData.append('auto_confirm', 'true');
    }

    // POST /sources - unified upload endpoint (returns data directly, not wrapped)
    // Upload timeout is longer than the default HTTP timeout because the
    // backend may retry commits with exponential backoff when SQLite is
    // locked by concurrent worker writes. Sourced from BatchSettings.
    const response = await apiClient.post<Source>('/sources', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: DEFAULT_PUBLIC_SETTINGS.batch_upload_timeout_ms,
      onUploadProgress: onUploadProgress
        ? (event) => {
            if (event.total) {
              onUploadProgress(Math.round((event.loaded / event.total) * 100));
            }
          }
        : undefined,
      signal,
    });
    return response.data;
  },

  uploadBatch: async (
    files: File[],
    extractEntities: boolean = true,
    analysisDepth: 'quick' | 'full' = 'full',
    enableNormalization: boolean = false,
    domain?: string,
    enableVision?: boolean | null,
    filteringMode?: string,
    contentFiltering: boolean = true,
    skipDuplicates: boolean = false,
  ): Promise<{ uploaded: number; failed: number; files: Source[]; errors: { filename: string; error: string }[] }> => {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));
    formData.append('extract_entities', extractEntities.toString());
    formData.append('analysis_depth', analysisDepth);
    formData.append('enable_normalization', enableNormalization.toString());
    formData.append('content_filtering', contentFiltering.toString());
    if (domain) {
      formData.append('domain', domain);
    }
    if (enableVision !== undefined && enableVision !== null) {
      formData.append('enable_vision', enableVision.toString());
    }
    if (filteringMode) {
      formData.append('filtering_mode', filteringMode);
    }
    if (skipDuplicates) {
      formData.append('skip_duplicates', 'true');
    }

    // POST /sources/batch - returns data directly
    // Batch uploads need an even longer timeout since each file commits
    // independently and may encounter lock contention.
    const response = await apiClient.post<{
      uploaded: number;
      failed: number;
      files: Source[];
      errors: { filename: string; error: string }[];
    }>('/sources/batch', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: DEFAULT_PUBLIC_SETTINGS.batch_batch_upload_timeout_ms,
    });
    return response.data;
  },

  /**
   * Abort all processing for a source.
   * DELETE /sources/{id}/processing
   */
  cancelProcessing: async (sourceId: string): Promise<{ success: boolean }> => {
    await apiClient.delete(`/sources/${sourceId}/processing`);
    return { success: true };
  },

  /**
   * Get extraction status and progress.
   * GET /sources/{id}/extraction
   */
  getExtraction: async (sourceId: string): Promise<SourceExtractionStatus> => {
    const response = await apiClient.get<SourceExtractionStatus>(
      `/sources/${sourceId}/extraction`
    );
    return response.data;
  },

  /**
   * Get paginated entities for a source.
   * GET /sources/{id}/entities
   */
  getEntities: async (
    sourceId: string,
    page: number = 1,
    perPage: number = DEFAULT_PUBLIC_SETTINGS.pagination_default_page_size,
    sortBy: string = 'default',
    sortOrder: string = 'desc',
  ): Promise<{
    entities: ExtractedEntity[];
    pagination: PaginationMetadata;
  }> => {
    const response = await apiClient.get<{
      entities: ExtractedEntity[];
      pagination: PaginationMetadata;
    }>(`/sources/${sourceId}/entities`, {
      params: { page, per_page: perPage, sort_by: sortBy, sort_order: sortOrder },
    });
    return response.data;
  },

  /**
   * Get paginated relationships for a source.
   * GET /sources/{id}/relationships
   */
  getRelationships: async (
    sourceId: string,
    page: number = 1,
    perPage: number = DEFAULT_PUBLIC_SETTINGS.pagination_default_page_size,
  ): Promise<{
    relationships: InferredRelationship[];
    pagination: PaginationMetadata;
  }> => {
    const response = await apiClient.get<{
      relationships: InferredRelationship[];
      pagination: PaginationMetadata;
    }>(`/sources/${sourceId}/relationships`, {
      params: { page, per_page: perPage },
    });
    return response.data;
  },

  /**
   * Get paginated templates for a source.
   * GET /sources/{id}/templates
   */
  getTemplates: async (
    sourceId: string,
    page: number = 1,
    perPage: number = DEFAULT_PUBLIC_SETTINGS.pagination_default_page_size,
  ): Promise<{
    templates: SourceTemplateSummary[];
    pagination: PaginationMetadata;
  }> => {
    const response = await apiClient.get<{
      templates: SourceTemplateSummary[];
      pagination: PaginationMetadata;
    }>(`/sources/${sourceId}/templates`, {
      params: { page, per_page: perPage },
    });
    return response.data;
  },

  // ========================================
  // Unified List (single HTTP call, maps Source → UnifiedSource)
  // ========================================

  listUnified: async (params?: {
    stage?: 'queued' | 'processing' | 'active' | 'all';
    status?: string;
    source_type?: string;
    search?: string;
  }): Promise<UnifiedSource[]> => {
    try {
      // GET /sources returns PaginatedSourcesResponse with data: SourceSummary[]
      const response = await apiClient.get<PaginatedSourcesResponse>('/sources', {
        params: {
          search: params?.search,
          source_type: params?.source_type,
          page_size: DEFAULT_PUBLIC_SETTINGS.batch_graph_source_page_size,
        },
      });

      return response.data.data
        .map((s: SourceSummary): UnifiedSource => mapSourceToUnified(s))
        .filter(u => !params?.stage || params.stage === 'all' || u.stage === params.stage)
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    } catch (err) {
      logger.error('Failed to fetch sources:', err);
      return [];
    }
  },

  /**
   * Manually retry an errored source.
   *
   * Backend resets the source to the stage just before the failure
   * (based on error_stage) and dispatches the next queue task. Clears
   * error_message, error_stage, and recovery_attempts.
   *
   * Preserves the cached commit_payload for commit-only retries (cheap —
   * no LLM tokens spent). Use {@link reextractSource} when you want to
   * throw away the payload and re-run the LLM.
   *
   * @throws HTTPError 404 if source not found.
   * @throws HTTPError 409 if source is not in 'error' state.
   */
  retrySource: async (sourceId: string): Promise<Source> => {
    const response = await apiClient.post<Source>(`/sources/${sourceId}/retry`);
    return response.data;
  },

  /**
   * Manually re-extract a source — discard cached extraction, re-run LLM.
   *
   * Distinct from {@link retrySource}: re-extract throws away the
   * cached commit_payload + extraction_results, resets the source to
   * 'indexed', and re-runs entity extraction. This is the expensive
   * action — it costs LLM tokens. Use it when the cached extraction
   * is wrong or the user wants to redo the analysis.
   *
   * Allowed source statuses: indexed, extracted, extracting, committing,
   * committed, error. Pending and indexing are rejected with 422.
   *
   * @throws HTTPError 404 if source not found.
   * @throws HTTPError 409 if source or system processing is paused.
   * @throws HTTPError 422 if status is pending or indexing.
   * @throws HTTPError 503 if no LLM provider is configured.
   */
  reextractSource: async (sourceId: string): Promise<Source> => {
    const response = await apiClient.post<Source>(
      `/sources/${sourceId}/re_extract`,
    );
    return response.data;
  },

  /**
   * Trigger entity extraction (or re-extraction) on a source.
   *
   * For sources in 'indexed' status, queues a normal extraction run.
   * For sources in 'committed' status, pass force=true to delete existing
   * graph artifacts and run a fresh extraction.
   *
   * POST /sources/{id}/extraction
   *
   * @throws HTTPError 404 if source not found.
   * @throws HTTPError 400 if source is not in a valid state for extraction.
   * @throws HTTPError 503 if no LLM provider is configured.
   */
  triggerExtraction: async (
    sourceId: string,
    options: {
      analysis_depth?: 'quick' | 'full';
      domain?: string;
      force?: boolean;
      filtering_mode?: string;
      content_filtering?: boolean;
    } = {},
  ): Promise<{ source_id: string; status: string }> => {
    const response = await apiClient.post<{ source_id: string; status: string }>(
      `/sources/${sourceId}/extraction`,
      options,
    );
    return response.data;
  },

  /**
   * Confirm the extraction domain + options for a parked
   * (awaiting_confirmation) source. CAS awaiting_confirmation → indexed on
   * the backend, then re-queues OP_IMPORT_ANALYSIS.
   *
   * POST /sources/{id}/confirmation
   * Body mirrors TriggerExtractionRequest (domain + analysis_depth +
   * filtering_mode + content_filtering + the four extraction toggles).
   *
   * @throws HTTPError 409 if the source is no longer awaiting_confirmation.
   */
  confirmExtraction: async (
    sourceId: string,
    options: ConfirmExtractionOptions,
  ): Promise<{ source_id: string; status: string }> => {
    const response = await apiClient.post<{ source_id: string; status: string }>(
      `/sources/${sourceId}/confirmation`,
      options,
    );
    return response.data;
  },

  /**
   * Bulk-confirm extraction for several parked sources in one call.
   *
   * Each source is confirmed independently with its detected domain and the
   * proposal's options (no per-item overrides — use the single
   * `confirmExtraction` endpoint for per-source overrides). Per-item failures
   * do not abort the batch.
   *
   * POST /sources/confirmation
   * Body: BulkConfirmExtractionRequest  { source_ids: string[] }
   * Response: BulkConfirmExtractionResponse { confirmed, failed, results[] }
   */
  bulkConfirmExtraction: async (
    sourceIds: string[],
  ): Promise<{ confirmed: number; failed: number; results: Array<{ source_id: string; ok: boolean; error?: string | null }> }> => {
    const response = await apiClient.post<{
      confirmed: number;
      failed: number;
      results: Array<{ source_id: string; ok: boolean; error?: string | null }>;
    }>('/sources/confirmation', { source_ids: sourceIds });
    return response.data;
  },

  /**
   * List sources parked awaiting confirmation. Thin wrapper over the unified
   * list with the awaiting status filter; used by the bulk-confirm flow and
   * the discoverability badge click target.
   *
   * GET /sources?status=awaiting_confirmation
   */
  listAwaiting: async (): Promise<UnifiedSource[]> => {
    const response = await apiClient.get<PaginatedSourcesResponse>('/sources', {
      params: {
        status: 'awaiting_confirmation',
        page_size: DEFAULT_PUBLIC_SETTINGS.batch_graph_source_page_size,
      },
    });
    return response.data.data.map((s: SourceSummary) => mapSourceToUnified(s));
  },

  /**
   * Get LLM queue statistics.
   * GET /llm/stats
   */
  getLlmStats: async (): Promise<{ data: Record<string, unknown> }> => {
    const response = await apiClient.get<{ data: Record<string, unknown> }>('/llm/stats');
    return response.data;
  },

  // ========================================
  // Chunk rerun (per-chunk rerun feature, 2026-05-15)
  // ========================================

  rerunChunk: async (
    sourceId: string,
    chunkIndex: number,
  ): Promise<ChunkRerunResponse> => {
    const response = await apiClient.post<ChunkRerunResponse>(
      `/sources/${sourceId}/chunks/${chunkIndex}/rerun`,
    );
    return response.data;
  },

  listChunkAttempts: async (
    sourceId: string,
    chunkIndex: number,
  ): Promise<ChunkAttemptsListResponse> => {
    const response = await apiClient.get<ChunkAttemptsListResponse>(
      `/sources/${sourceId}/chunks/${chunkIndex}/attempts`,
    );
    return response.data;
  },

  getChunkAttempt: async (
    sourceId: string,
    chunkIndex: number,
    attemptId: string,
  ): Promise<ChunkAttemptDetail> => {
    const response = await apiClient.get<ChunkAttemptDetail>(
      `/sources/${sourceId}/chunks/${chunkIndex}/attempts/${attemptId}`,
    );
    return response.data;
  },

  /**
   * Fetch multiple small chunks by ID — used by ChunkSourceDataPanel for
   * the raw-vs-cleaned view.
   * GET /sources/{source_id}/chunks/batch?ids=<csv>
   */
  getChunksByIds: async (
    sourceId: string,
    ids: string[],
  ): Promise<{ chunks: SmallChunk[] }> => {
    const response = await apiClient.get<{ chunks: SmallChunk[] }>(
      `/sources/${sourceId}/chunks/batch`,
      { params: { ids: ids.join(',') } },
    );
    return response.data;
  },
};

// ============================================================================
// Types for chunk rerun + attempts (per-chunk rerun feature, 2026-05-15)
// ============================================================================

export interface ChunkRerunResponse {
  chunk_task_id: string;
  queue_task_id: string;
  attempt_number: number;
  source_status: string;
}

export interface ChunkAttemptSummary {
  id: string;
  chunk_task_id: string;
  attempt_number: number;
  snapshotted_at: string;
  started_at: string | null;
  completed_at: string | null;
  entity_count: number;
  relationship_count: number;
  invalid_relationship_count: number;
  finish_reason: string | null;
  aborted_by_loop: boolean | null;
  llm_duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  input_text_length: number | null;
  llm_response_length: number | null;
  error_message: string | null;
  error_type: string | null;
}

export interface ChunkAttemptDetail extends ChunkAttemptSummary {
  input_text: string | null;
  llm_response_json: string | null;
  raw_entities: Array<Record<string, unknown>> | null;
  raw_relationships: Array<Record<string, unknown>> | null;
  filtering_log: Record<string, unknown> | null;
  chunk_sentences: string[] | null;
}

export interface ChunkAttemptsListResponse {
  data: ChunkAttemptSummary[];
}

export interface SmallChunk {
  id: string;
  content: string;
  chunk_index: number;
}
