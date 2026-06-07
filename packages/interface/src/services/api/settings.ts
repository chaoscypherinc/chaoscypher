// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient } from './client';
import { enqueueAndWait } from './queue';
import type { Settings, SettingsUpdateResponse, VRAMPreset, PresetListResponse, ApplyPresetResponse, OllamaVerifyResponse, LLMProvider, LLMVerifyResponse, LLMHealthResponse, CloudModelsResponse, OllamaModelsListResponse, OllamaModelShowResponse } from '../../types';

/**
 * Shape returned by the backend after a reset/cleanup is finalized on
 * the worker. Exact keys vary by operation (e.g. knowledge-base reset
 * returns counts for sources/chunks/nodes/edges; cleanup/orphans returns
 * edges/nodes/templates scanned + removed), so typed as an open record.
 */
type QueuedResetResult = { status: string; [key: string]: unknown };

const unwrap = <T>(payload: unknown): T => {
  if (payload && typeof payload === 'object' && 'data' in payload) {
    return (payload as { data: T }).data;
  }
  return payload as T;
};

export const settingsApi = {
  get: async (): Promise<Settings> => {
    const response = await apiClient.get<Settings | { data: Settings }>('/settings');
    return unwrap<Settings>(response.data);
  },
  update: async (updates: Partial<Settings>): Promise<SettingsUpdateResponse> => {
    const response = await apiClient.patch<SettingsUpdateResponse | { data: SettingsUpdateResponse }>('/settings', updates);
    return unwrap<SettingsUpdateResponse>(response.data);
  },
  getAccessHint: async (): Promise<{ request_host: string; is_loopback: boolean }> => {
    const response = await apiClient.get<{ request_host: string; is_loopback: boolean }>(
      '/settings/host',
    );
    return response.data;
  },
  reset: async (): Promise<Settings> => {
    const response = await apiClient.post<Settings | { data: Settings }>('/settings/reset');
    return unwrap<Settings>(response.data);
  },
  // Database reset operations
  resetWorkflows: async (): Promise<{ status: string; [key: string]: unknown }> => {
    const response = await apiClient.post<{ data: { status: string; [key: string]: unknown } }>(
      '/settings/reset/workflows'
    );
    return unwrap(response.data);
  },
  resetChats: async (): Promise<{ status: string; chats_deleted: number }> => {
    const response = await apiClient.post<{ data: { status: string; chats_deleted: number } }>(
      '/settings/reset/chats'
    );
    return unwrap(response.data);
  },
  resetSourceProcessing: async (): Promise<{ status: string; [key: string]: unknown }> => {
    const response = await apiClient.post<{ data: { status: string; [key: string]: unknown } }>(
      '/settings/reset/source_processing'
    );
    return unwrap(response.data);
  },
  resetDiscovery: async (): Promise<{ status: string; [key: string]: unknown }> => {
    const response = await apiClient.post<{ data: { status: string; [key: string]: unknown } }>(
      '/settings/reset/discovery'
    );
    return unwrap(response.data);
  },
  resetQueue: async (): Promise<{ status: string; [key: string]: unknown }> => {
    const response = await apiClient.post<{ data: { status: string; [key: string]: unknown } }>(
      '/settings/reset/queue'
    );
    return unwrap(response.data);
  },
  // /reset/knowledge, /reset/all, /cleanup/orphans moved from sync-blocking
  // endpoints to queue + 202 on 2026-04-18. The backend returns a task_id;
  // we poll /queue/tasks/{id}/result to surface the original counts payload
  // to callers. This preserves the pre-2026-04-18 API shape for consumers.
  resetKnowledge: async (signal?: AbortSignal): Promise<QueuedResetResult> => {
    return enqueueAndWait<QueuedResetResult>(
      () => apiClient.post('/settings/reset/knowledge'),
      { signal },
    );
  },
  resetAll: async (confirmation: string, signal?: AbortSignal): Promise<QueuedResetResult> => {
    return enqueueAndWait<QueuedResetResult>(
      () => apiClient.post('/settings/reset/all', { confirmation }),
      { signal },
    );
  },
  // Cleanup operations (queue + 202 since 2026-04-18).
  cleanupOrphans: async (signal?: AbortSignal): Promise<QueuedResetResult> => {
    return enqueueAndWait<QueuedResetResult>(
      () => apiClient.post('/settings/cleanup/orphans'),
      { signal },
    );
  },

  // VRAM Preset operations
  listPresets: async (): Promise<PresetListResponse> => {
    const response = await apiClient.get<PresetListResponse | { data: PresetListResponse }>(
      '/settings/presets'
    );
    return unwrap<PresetListResponse>(response.data);
  },
  getPreset: async (presetId: string): Promise<VRAMPreset> => {
    const response = await apiClient.get<VRAMPreset | { data: VRAMPreset }>(
      `/settings/presets/${presetId}`
    );
    return unwrap<VRAMPreset>(response.data);
  },
  applyPreset: async (presetId: string): Promise<ApplyPresetResponse> => {
    const response = await apiClient.post<ApplyPresetResponse | { data: ApplyPresetResponse }>(
      '/settings/presets/apply',
      { preset_id: presetId }
    );
    return unwrap<ApplyPresetResponse>(response.data);
  },

  // Ollama verification
  verifyOllamaUrl: async (url: string, timeout: number = 5): Promise<OllamaVerifyResponse> => {
    const response = await apiClient.post<OllamaVerifyResponse | { data: OllamaVerifyResponse }>(
      '/settings/ollama/verify',
      { url, timeout }
    );
    return unwrap<OllamaVerifyResponse>(response.data);
  },

  // Cloud LLM verification (openai / anthropic / gemini)
  verifyLLM: async (provider: LLMProvider, apiKey: string): Promise<LLMVerifyResponse> => {
    const response = await apiClient.post<LLMVerifyResponse | { data: LLMVerifyResponse }>(
      '/settings/llm/verify',
      { provider, api_key: apiKey }
    );
    return unwrap<LLMVerifyResponse>(response.data);
  },

  // LLM health snapshot — drives the import/chat action gates + global banner.
  getLLMHealth: async (): Promise<LLMHealthResponse> => {
    const response = await apiClient.get<LLMHealthResponse | { data: LLMHealthResponse }>(
      '/settings/llm/health'
    );
    return unwrap<LLMHealthResponse>(response.data);
  },

  // Cloud model registry
  getCloudModels: async (): Promise<CloudModelsResponse> => {
    const response = await apiClient.get<CloudModelsResponse | { data: CloudModelsResponse }>(
      '/settings/cloudmodels'
    );
    return unwrap<CloudModelsResponse>(response.data);
  },

  // Ollama model management
  listOllamaModels: async (): Promise<OllamaModelsListResponse> => {
    const response = await apiClient.get('/settings/ollama/models');
    return unwrap(response.data);
  },

  pullOllamaModel: async (model: string, instanceId?: string, signal?: AbortSignal): Promise<Response> => {
    // Use fetch (not EventSource) since this is a POST endpoint with SSE response
    const url = `${apiClient.defaults.baseURL}/settings/ollama/models/pull`;
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, instance_id: instanceId }),
      credentials: 'include',
      signal,
    });
  },

  removeOllamaModel: async (model: string, instanceId?: string): Promise<{ success: boolean }> => {
    const response = await apiClient.delete('/settings/ollama/models/remove', {
      data: { model, instance_id: instanceId },
    });
    return unwrap(response.data);
  },

  showOllamaModel: async (model: string, instanceId?: string): Promise<OllamaModelShowResponse> => {
    const encodedModel = encodeURIComponent(model);
    const params = instanceId ? `?instance_id=${instanceId}` : '';
    const response = await apiClient.get(`/settings/ollama/models/${encodedModel}/details${params}`);
    return unwrap(response.data);
  },

  // Local embedding model management
  listLocalEmbeddingModels: async (): Promise<{ models: Array<{ id: string; name: string; path: string }> }> => {
    const response = await apiClient.get('/settings/embedding/local/models');
    return unwrap(response.data);
  },

  downloadLocalEmbeddingModel: async (model: string): Promise<{ model_name: string; native_dimensions: number; download_time_ms: number }> => {
    const response = await apiClient.post('/settings/embedding/local/models', { model });
    return unwrap(response.data);
  },

  deleteLocalEmbeddingModel: async (modelId: string): Promise<{ status: string; model: string }> => {
    const encodedModel = encodeURIComponent(modelId);
    const response = await apiClient.delete(`/settings/embedding/local/models/${encodedModel}`);
    return unwrap(response.data);
  },
};
