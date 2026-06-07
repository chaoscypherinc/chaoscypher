// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useOllamaModels: Hook for fetching and managing Ollama models.
 *
 * Provides model listing, pull progress tracking, and removal
 * across configured Ollama instances.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { settingsApi } from '../services/api/settings';
import type { OllamaModelsListResponse, OllamaModelShowResponse } from '../types/settings';
import { isAbortError } from '../utils/errors';
import { logger } from '../utils/logger';

interface PullProgress {
  model: string;
  instanceId: string;
  status: string;
  completed: number;
  total: number;
}

interface UseOllamaModelsResult {
  /** Models grouped by instance */
  modelsData: OllamaModelsListResponse | null;
  /** Whether models are being loaded */
  loading: boolean;
  /** Set of all installed model names (across all instances) */
  installedModels: Set<string>;
  /** Active pull progress by model name */
  pullProgress: Record<string, PullProgress>;
  /** Refresh the models list */
  refresh: () => Promise<void>;
  /** Pull a model */
  pullModel: (model: string, instanceId?: string) => Promise<void>;
  /** Remove a model */
  removeModel: (model: string, instanceId?: string) => Promise<boolean>;
  /** Get model details */
  showModel: (model: string, instanceId?: string) => Promise<OllamaModelShowResponse>;
}

export function useOllamaModels(enabled: boolean = true): UseOllamaModelsResult {
  const [modelsData, setModelsData] = useState<OllamaModelsListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [pullProgress, setPullProgress] = useState<Record<string, PullProgress>>({});
  const pullAbortController = useRef<AbortController | null>(null);
  const progressClearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (progressClearTimer.current) clearTimeout(progressClearTimer.current);
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    try {
      const data = await settingsApi.listOllamaModels();
      setModelsData(data);
    } catch (error) {
      logger.error('Failed to load Ollama models:', error);
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  // Load on mount
  useEffect(() => {
    if (enabled) {
      refresh();
    }
  }, [enabled, refresh]);

  // Build installed models set
  const installedModels = useMemo(() => {
    const set = new Set<string>();
    if (modelsData) {
      for (const instance of modelsData.instances) {
        for (const model of instance.models) {
          set.add(model.name);
        }
      }
    }
    return set;
  }, [modelsData]);

  const pullModel = useCallback(async (model: string, instanceId?: string) => {
    // Abort any in-flight pull before starting a new one
    pullAbortController.current?.abort();
    const controller = new AbortController();
    pullAbortController.current = controller;

    try {
      const response = await settingsApi.pullOllamaModel(model, instanceId, controller.signal);

      if (!response.ok || !response.body) {
        throw new Error(`Pull failed: ${response.statusText}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const text = decoder.decode(value, { stream: true });
          for (const line of text.split('\n')) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6));
                const instId = data.instance_id || instanceId || 'default';

                if (data.status === 'error') {
                  logger.error('Pull error:', data.error);
                  setPullProgress(prev => {
                    const next = { ...prev };
                    delete next[model];
                    return next;
                  });
                } else {
                  setPullProgress(prev => ({
                    ...prev,
                    [model]: {
                      model,
                      instanceId: instId,
                      status: data.status || '',
                      completed: data.completed || 0,
                      total: data.total || 0,
                    },
                  }));

                  if (data.status === 'success') {
                    // Clear progress after brief delay, then refresh
                    if (progressClearTimer.current) clearTimeout(progressClearTimer.current);
                    progressClearTimer.current = setTimeout(() => {
                      setPullProgress(prev => {
                        const next = { ...prev };
                        delete next[model];
                        return next;
                      });
                    }, 1000);
                    await refresh();
                  }
                }
              } catch {
                // Skip malformed SSE lines
              }
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    } catch (error) {
      if (isAbortError(error)) return;
      logger.error('Pull failed:', error);
      setPullProgress(prev => {
        const next = { ...prev };
        delete next[model];
        return next;
      });
    }
  }, [refresh]);

  const removeModel = useCallback(async (model: string, instanceId?: string): Promise<boolean> => {
    try {
      const result = await settingsApi.removeOllamaModel(model, instanceId);
      if (result.success) {
        await refresh();
      }
      return result.success;
    } catch (error) {
      logger.error('Remove failed:', error);
      return false;
    }
  }, [refresh]);

  const showModel = useCallback(async (
    model: string, instanceId?: string
  ): Promise<OllamaModelShowResponse> => {
    return settingsApi.showOllamaModel(model, instanceId);
  }, []);

  // Abort in-flight pull on unmount
  useEffect(() => {
    return () => {
      pullAbortController.current?.abort();
    };
  }, []);

  return {
    modelsData,
    loading,
    installedModels,
    pullProgress,
    refresh,
    pullModel,
    removeModel,
    showModel,
  };
}
