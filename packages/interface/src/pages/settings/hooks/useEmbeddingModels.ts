// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useEmbeddingModels: Fetches the embedding model registry from the API.
 *
 * Returns curated (local/ollama) models and cloud provider models
 * keyed by provider name. Used by EmbeddingModelSelector to populate
 * model options for all provider types.
 *
 * Also exposes the local (sentence-transformers) model lifecycle as
 * TanStack Query hooks — listing, download, delete — so the selector's
 * download/delete actions invalidate the cached list instead of manually
 * re-fetching into component state.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../../../services/api/client';
import { settingsApi } from '../../../services/api/settings';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CuratedEmbeddingModel {
  name: string;
  local: string;
  ollama: string;
  dimensions: number;
  mrl: boolean;
  default: boolean;
}

export interface CloudEmbeddingModel {
  name: string;
  model: string;
  dimensions: number;
  mrl: boolean;
  current: boolean;
}

interface EmbeddingModelsResponse {
  curated: CuratedEmbeddingModel[];
  cloud: Record<string, CloudEmbeddingModel[]>;
}

export interface EmbeddingOption {
  id: string;
  name: string;
  description: string;
  group: string;
  installed?: boolean;
}

interface LocalEmbeddingModel {
  id: string;
  name: string;
  path: string;
}

// ---------------------------------------------------------------------------
// Query keys (module-local)
// ---------------------------------------------------------------------------

const EMBEDDING_MODELS_QUERY_KEY = ['settings', 'embedding', 'models'] as const;
const LOCAL_EMBEDDING_MODELS_QUERY_KEY = ['settings', 'embedding', 'local-models'] as const;

// ---------------------------------------------------------------------------
// Registry hook
// ---------------------------------------------------------------------------

/**
 * Fetch the embedding model registry. Returns `null` until loaded (or on
 * error) to preserve the original component contract — callers fall back to
 * empty curated/cloud lists when this is null.
 */
export function useEmbeddingModels(): EmbeddingModelsResponse | null {
  const { data } = useQuery<EmbeddingModelsResponse>({
    queryKey: EMBEDDING_MODELS_QUERY_KEY,
    queryFn: async () => {
      const res = await apiClient.get<EmbeddingModelsResponse>('/settings/embedding/models');
      return res.data;
    },
    staleTime: 5 * 60_000,
  });
  return data ?? null;
}

// ---------------------------------------------------------------------------
// Local embedding model lifecycle
// ---------------------------------------------------------------------------

/**
 * List downloaded local (sentence-transformers) embedding models. Only
 * enabled when the active provider is `local`.
 */
export function useLocalEmbeddingModels(enabled: boolean) {
  return useQuery<LocalEmbeddingModel[]>({
    queryKey: LOCAL_EMBEDDING_MODELS_QUERY_KEY,
    queryFn: async () => {
      const data = await settingsApi.listLocalEmbeddingModels();
      return data.models;
    },
    enabled,
  });
}

/** Download a local embedding model, then refresh the downloaded list. */
export function useDownloadLocalEmbeddingModel() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: (modelId) => settingsApi.downloadLocalEmbeddingModel(modelId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: LOCAL_EMBEDDING_MODELS_QUERY_KEY });
    },
  });
}

/** Delete a downloaded local embedding model, then refresh the list. */
export function useDeleteLocalEmbeddingModel() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: (modelId) => settingsApi.deleteLocalEmbeddingModel(modelId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: LOCAL_EMBEDDING_MODELS_QUERY_KEY });
    },
  });
}
