// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { NavigateFunction } from 'react-router';
import { sourcesApi } from '../../../services/api/sources';
import { chatApi } from '../../../services/api/chat';
import { apiClient } from '../../../services/api/client';
import type { Source, SourceStats } from '../../../types';
import {
  isSourceCommitted,
  isSourceProcessing,
} from '../../../types';
import { getApiErrorMessage } from '../../../utils/errors';

export interface ExtractionProgress {
  total_chunks: number;
  completed_chunks: number;
  failed_chunks: number;
  progress_percent: number;
  started_at?: string;
  timing?: {
    estimated_remaining_seconds: number | null;
    elapsed_seconds: number | null;
    avg_chunk_time_seconds: number | null;
    sample_count: number | null;
  };
  current_chunk?: {
    chunk_index: number;
    retry_count: number;
    max_retries: number;
    elapsed_seconds: number | null;
  } | null;
}

interface UseSourceDetailReturn {
  source: Source | null;
  stats: SourceStats | null;
  loading: boolean;
  loadError: string | null;
  actionError: string | null;
  clearActionError: () => void;
  extractionProgress: ExtractionProgress | null;
  setSource: (s: Source) => void;
  refetch: () => Promise<void>;
  deleteSource: () => Promise<void>;
  toggleEnabled: () => Promise<void>;
  abortProcessing: () => Promise<void>;
  resetToIndexed: () => Promise<void>;
  finalizePartial: () => Promise<void>;
  chatWithSource: () => Promise<void>;
  retrySource: () => Promise<void>;
  reExtract: (force: boolean) => Promise<void>;
  /**
   * Audit fix #F49 — explicit Re-extract action distinct from Retry.
   *
   * Routes through ``POST /sources/{id}/re_extract`` which always
   * discards the cached ``commit_payload`` + extraction results and
   * re-runs the LLM. Differs from {@link reExtract}/triggerExtraction
   * by handling all post-INDEXING statuses uniformly (extracted,
   * extracting, committing, error) — not just indexed/committed.
   */
  reextractSource: () => Promise<void>;
}

// Poll interval (ms) while the source is in a processing state. Matches the
// 3s cadence the old `usePolling` loop used.
const PROCESSING_POLL_MS = 3000;

// Module-local query keys. Not exported — exporting trips knip. These slot
// under the `['source', id, …]` convention so source-scoped mutations
// (e.g. useChunkRerun) cascade to them on invalidation.
function sourceDetailQueryKey(id: string) {
  return ['source', id] as const;
}
function sourceStatsQueryKey(id: string) {
  return ['source', id, 'stats'] as const;
}
function sourceExtractionProgressQueryKey(id: string) {
  return ['source', id, 'extraction-progress'] as const;
}

interface ExtractionProgressResponse {
  has_extraction_job?: boolean;
  [key: string]: unknown;
}

/**
 * Encapsulates SourcePage data loading, polling for processing
 * sources, extraction progress fetching, and all mutation actions.
 * The caller supplies `navigate` so the hook stays easy to test.
 *
 * Migrated from raw fetch+useState+useEffect+usePolling to TanStack
 * Query: the manual 3s poll loop is now a data-driven `refetchInterval`
 * that returns `PROCESSING_POLL_MS` while the source is in a processing
 * state and `false` once it reaches a terminal status. The extraction
 * progress fetch is its own query, enabled + polled only while the
 * source is `extracting`.
 */
export function useSourceDetail(
  id: string | undefined,
  navigate: NavigateFunction,
): UseSourceDetailReturn {
  const qc = useQueryClient();
  const [actionError, setActionError] = useState<string | null>(null);

  const sourceQuery = useQuery<Source>({
    queryKey: id ? sourceDetailQueryKey(id) : ['source', 'none'],
    queryFn: () => sourcesApi.get(id as string),
    enabled: !!id,
    // Poll while processing; stop at a terminal status. Evaluated from the
    // latest query data so it self-disables the moment the source settles.
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && isSourceProcessing(data) ? PROCESSING_POLL_MS : false;
    },
  });

  const source = sourceQuery.data ?? null;

  const statsQuery = useQuery<SourceStats>({
    queryKey: id ? sourceStatsQueryKey(id) : ['source', 'none', 'stats'],
    queryFn: () => sourcesApi.getStats(id as string),
    // Stats only exist once the source is committed.
    enabled: !!id && !!source && isSourceCommitted(source),
  });

  // Extraction progress: only meaningful (and only polled) while extracting.
  const isExtracting = source?.status === 'extracting';
  const extractionProgressQuery = useQuery<ExtractionProgress | null>({
    queryKey: id
      ? sourceExtractionProgressQueryKey(id)
      : ['source', 'none', 'extraction-progress'],
    queryFn: async () => {
      const response = await apiClient.get<ExtractionProgressResponse>(
        `/sources/${id}/extraction`,
      );
      const progressData = response.data;
      // Only surface progress once the backend reports a live extraction job;
      // otherwise keep the banner's progress block hidden (matches old logic).
      return progressData.has_extraction_job
        ? (progressData as unknown as ExtractionProgress)
        : null;
    },
    enabled: !!id && isExtracting,
    refetchInterval: isExtracting ? PROCESSING_POLL_MS : false,
  });

  // The old hook cleared progress whenever status left `extracting`; mirror
  // that by only exposing progress while extracting.
  const extractionProgress = isExtracting
    ? extractionProgressQuery.data ?? null
    : null;

  const loadError = sourceQuery.isError
    ? 'Failed to load source: ' + getApiErrorMessage(sourceQuery.error)
    : null;

  const refetch = useCallback(async () => {
    if (!id) return;
    await qc.invalidateQueries({ queryKey: sourceDetailQueryKey(id) });
  }, [id, qc]);

  // setSource — kept for interface compatibility. Writes straight into the
  // query cache so any optimistic caller stays in sync with the query.
  const setSource = useCallback(
    (s: Source) => {
      if (!id) return;
      qc.setQueryData(sourceDetailQueryKey(id), s);
    },
    [id, qc],
  );

  // ---- Actions --------------------------------------------------------

  const deleteSource = useCallback(async () => {
    if (!id) return;
    try {
      await sourcesApi.delete(id);
      navigate('/sources');
    } catch (err) {
      setActionError('Delete failed: ' + getApiErrorMessage(err));
    }
  }, [id, navigate]);

  const toggleEnabled = useCallback(async () => {
    if (!source || !id) return;
    try {
      const updated = await sourcesApi.update(id, { enabled: !source.enabled });
      setSource(updated);
    } catch (err) {
      setActionError('Failed to toggle enabled status: ' + getApiErrorMessage(err));
    }
  }, [source, id, setSource]);

  const abortProcessing = useCallback(async () => {
    if (!source || !id) return;
    try {
      await apiClient.delete(`/sources/${id}/processing`);
      await refetch();
    } catch (err) {
      setActionError('Failed to abort: ' + getApiErrorMessage(err));
    }
  }, [source, id, refetch]);

  const resetToIndexed = useCallback(async () => {
    if (!source || !id) return;
    try {
      await sourcesApi.update(id, { processing_status: 'ready' });
      await refetch();
    } catch (err) {
      setActionError('Failed to reset: ' + getApiErrorMessage(err));
    }
  }, [source, id, refetch]);

  const finalizePartial = useCallback(async () => {
    if (!source || !id) return;
    try {
      await apiClient.post(`/sources/${id}/extraction`, { force: true });
      await refetch();
    } catch (err) {
      setActionError('Failed to finalize: ' + getApiErrorMessage(err));
    }
  }, [source, id, refetch]);

  const chatWithSource = useCallback(async () => {
    if (!source) return;
    try {
      const newChat = await chatApi.createChat({
        title: `Chat: ${source.title || source.filename}`,
        source_ids: [source.id],
      });
      navigate(`/chat/${newChat.id}`);
    } catch (err) {
      setActionError('Failed to create chat: ' + getApiErrorMessage(err));
    }
  }, [source, navigate]);

  const retrySource = useCallback(async () => {
    if (!id) return;
    try {
      const updated = await sourcesApi.retrySource(id);
      setSource(updated);
    } catch (err) {
      setActionError('Retry failed: ' + getApiErrorMessage(err));
    }
  }, [id, setSource]);

  const reExtract = useCallback(async (force: boolean) => {
    if (!id) return;
    try {
      await sourcesApi.triggerExtraction(id, { force });
      await refetch();
    } catch (err) {
      setActionError('Re-extraction failed: ' + getApiErrorMessage(err));
    }
  }, [id, refetch]);

  const reextractSource = useCallback(async () => {
    if (!id) return;
    try {
      await sourcesApi.reextractSource(id);
      await refetch();
    } catch (err) {
      setActionError('Re-extraction failed: ' + getApiErrorMessage(err));
    }
  }, [id, refetch]);

  const clearActionError = useCallback(() => setActionError(null), []);

  return {
    source,
    stats: statsQuery.data ?? null,
    loading: sourceQuery.isLoading,
    loadError,
    actionError,
    clearActionError,
    extractionProgress,
    setSource,
    refetch,
    deleteSource,
    toggleEnabled,
    abortProcessing,
    resetToIndexed,
    finalizePartial,
    chatWithSource,
    retrySource,
    reExtract,
    reextractSource,
  };
}
