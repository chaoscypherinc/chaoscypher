// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Settings, OllamaInstance, VRAMPreset, OllamaVerifyResponse, CloudModelsResponse } from '../../../types';
import { settingsApi } from '../../../services/api/settings';
import { logger } from '../../../utils/logger';
import { LLM_HEALTH_KEY } from '../../../hooks/useLLMHealth';

/** Generate a unique ID for new Ollama instances. */
const generateInstanceId = () => `instance_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

/** Shape of the new instance form state. */
interface NewInstanceForm {
  name: string;
  base_url: string;
}

/** Return type for the useProviderSettings hook. */
interface ProviderSettingsState {
  // New instance form
  newInstance: NewInstanceForm;
  setNewInstance: (value: NewInstanceForm) => void;

  // VRAM presets
  presets: VRAMPreset[];
  applyingPreset: boolean;
  presetMessage: { type: 'success' | 'error' | 'warning'; text: string } | null;
  setPresetMessage: (value: { type: 'success' | 'error' | 'warning'; text: string } | null) => void;

  // Advanced toggle
  showAdvanced: boolean;
  setShowAdvanced: (value: boolean) => void;

  // Ollama URL verification
  verifyingUrl: boolean;
  urlVerification: OllamaVerifyResponse | null;
  clearUrlVerification: () => void;

  // Cloud models registry
  cloudModels: CloudModelsResponse | null;

  // Computed values
  ollamaInstances: OllamaInstance[];
  enabledInstanceCount: number;
  primaryOllamaUrl: string;
  currentPreset: VRAMPreset | undefined;

  // Handlers
  handleApplyPreset: (presetId: string) => Promise<void>;
  handleVerifyOllamaUrl: () => Promise<void>;
  handleUrlChange: (newUrl: string) => void;
  handleAddInstance: () => void;
  handleRemoveInstance: (instanceId: string) => void;
  handleToggleInstance: (instanceId: string) => void;
  /**
   * Switch chat provider and seed the new provider's recommended model into
   * chat / extraction / vision fields. No-op model seeding for ollama (the
   * VRAM preset handles those).
   */
  handleChatProviderChange: (newProvider: string) => void;
}

/**
 * Custom hook that manages all provider settings state and side effects.
 *
 * Encapsulates VRAM preset fetching/applying, Ollama URL verification,
 * cloud model registry fetching, and instance CRUD operations.
 */
export function useProviderSettings(
  settings: Settings,
  setSettings: (settings: Settings) => void,
): ProviderSettingsState {
  const queryClient = useQueryClient();

  // State for new instance form
  const [newInstance, setNewInstance] = useState<NewInstanceForm>({
    name: '',
    base_url: '',
  });

  // State for VRAM presets
  const [presets, setPresets] = useState<VRAMPreset[]>([]);
  const [applyingPreset, setApplyingPreset] = useState(false);
  const [presetMessage, setPresetMessage] = useState<{ type: 'success' | 'error' | 'warning'; text: string } | null>(null);

  // State for showing advanced options
  const [showAdvanced, setShowAdvanced] = useState(false);

  // State for Ollama URL verification
  const [verifyingUrl, setVerifyingUrl] = useState(false);
  const [urlVerification, setUrlVerification] = useState<OllamaVerifyResponse | null>(null);

  // State for cloud models registry
  const [cloudModels, setCloudModels] = useState<CloudModelsResponse | null>(null);

  // Fetch cloud models on mount
  useEffect(() => {
    const fetchCloudModels = async () => {
      try {
        const response = await settingsApi.getCloudModels();
        setCloudModels(response);
      } catch (error) {
        logger.error('Failed to fetch cloud models:', error);
      }
    };
    fetchCloudModels();
  }, []);

  // Fetch presets on mount
  useEffect(() => {
    const fetchPresets = async () => {
      try {
        const response = await settingsApi.listPresets();
        setPresets(response.presets);
      } catch (error) {
        logger.error('Failed to fetch presets:', error);
      }
    };
    fetchPresets();
  }, []);

  // Handle applying a preset
  const handleApplyPreset = async (presetId: string) => {
    if (!presetId) return;
    setApplyingPreset(true);
    setPresetMessage(null);
    try {
      const response = await settingsApi.applyPreset(presetId);
      if (response.success) {
        const missing = response.missing_models ?? [];
        if (missing.length === 0) {
          setPresetMessage({ type: 'success', text: response.message });
        } else {
          setPresetMessage({
            type: 'warning',
            text:
              `${response.message} Pull required: ${missing.join(', ')}.`,
          });
        }
        // Invalidate llm/health so the banner + Add Source gate update
        // immediately instead of waiting for the 30s refetch tick.
        queryClient.invalidateQueries({ queryKey: LLM_HEALTH_KEY });

        // Refresh settings and set the preset selection
        const updatedSettings = await settingsApi.get();
        setSettings({
          ...updatedSettings,
          llm: {
            ...updatedSettings.llm,
            // Preserve the user's in-flight Ollama URL/instance edits. On
            // the setup wizard the URL only lives in the client working
            // draft until Finish, so a fresh GET here returns the server's
            // stale default and would otherwise clobber what the user just
            // typed. The preset endpoint only writes model/context fields.
            ollama_instances: settings.llm.ollama_instances,
            ollama_quick_preset: presetId,
          },
        });
      }
    } catch (_error) {
      setPresetMessage({ type: 'error', text: 'Failed to apply preset' });
    } finally {
      setApplyingPreset(false);
    }
  };

  // The "single Ollama URL" form edits the first instance's base_url. The
  // backend always seeds a default instance, so instances[0] is guaranteed
  // to exist in practice.
  const primaryOllamaUrl = settings.llm.ollama_instances[0]?.base_url ?? '';

  // Handle Ollama URL verification
  const handleVerifyOllamaUrl = async () => {
    if (!primaryOllamaUrl) return;

    setVerifyingUrl(true);
    setUrlVerification(null);
    try {
      const result = await settingsApi.verifyOllamaUrl(primaryOllamaUrl);
      setUrlVerification(result);
    } catch (_error) {
      setUrlVerification({
        success: false,
        message: 'Failed to verify URL',
        error_type: 'request_failed',
      });
    } finally {
      setVerifyingUrl(false);
    }
  };

  // Clear verification status when URL changes; rewrite instances[0].base_url.
  const handleUrlChange = (newUrl: string) => {
    const existing = settings.llm.ollama_instances;
    const updated: OllamaInstance[] = existing.length > 0
      ? existing.map((inst, i) => (i === 0 ? { ...inst, base_url: newUrl } : inst))
      : [
          {
            id: 'default',
            name: 'Default',
            base_url: newUrl,
            enabled: true,
            healthy: true,
          },
        ];
    setSettings({
      ...settings,
      llm: { ...settings.llm, ollama_instances: updated },
    });
    setUrlVerification(null);
  };

  // Add a new Ollama instance
  const handleAddInstance = () => {
    if (!newInstance.name.trim() || !newInstance.base_url.trim()) return;

    const instance = {
      id: generateInstanceId(),
      name: newInstance.name.trim(),
      base_url: newInstance.base_url.trim(),
      enabled: true,
      healthy: true,
    };

    const currentInstances = settings.llm.ollama_instances || [];
    setSettings({
      ...settings,
      llm: {
        ...settings.llm,
        ollama_instances: [...currentInstances, instance],
      },
    });

    // Clear the form
    setNewInstance({ name: '', base_url: '' });
  };

  // Remove an instance
  const handleRemoveInstance = (instanceId: string) => {
    const currentInstances = settings.llm.ollama_instances || [];
    setSettings({
      ...settings,
      llm: {
        ...settings.llm,
        ollama_instances: currentInstances.filter((i) => i.id !== instanceId),
      },
    });
  };

  // Toggle instance enabled state
  const handleToggleInstance = (instanceId: string) => {
    const currentInstances = settings.llm.ollama_instances || [];
    setSettings({
      ...settings,
      llm: {
        ...settings.llm,
        ollama_instances: currentInstances.map((i) =>
          i.id === instanceId ? { ...i, enabled: !i.enabled } : i
        ),
      },
    });
  };

  // Computed values
  const ollamaInstances = settings.llm.ollama_instances || [];
  const enabledInstanceCount = ollamaInstances.filter((i) => i.enabled).length;
  const currentPreset = presets.find(p => p.name === settings.llm.ollama_quick_preset);

  // Clear URL verification state
  const clearUrlVerification = () => setUrlVerification(null);

  /**
   * Switch the active chat provider AND stamp in the registry's recommended
   * chat / extraction / vision models for the new provider.
   *
   * Without this, `settings.llm.<provider>_chat_model` keeps whatever stale
   * value it had (typically the backend Pydantic default, e.g. `gpt-4.1`),
   * so the user has to manually pick the latest model every time they
   * switch providers. We pick:
   *
   *   - Chat / Extraction model: the cloud entry flagged `recommended: true`
   *     (or the first model as a fallback).
   *   - Vision model: the recommended model if it supports vision; otherwise
   *     the first model in the list that does.
   *
   * Ollama is left alone — its model fields are driven by the VRAM preset
   * via {@link handleApplyPreset}, and there's no cloud-style "recommended"
   * concept for local models.
   */
  const handleChatProviderChange = (newProvider: string) => {
    if (newProvider === 'ollama' || !cloudModels) {
      setSettings({ ...settings, llm: { ...settings.llm, chat_provider: newProvider } });
      return;
    }

    const providerEntry = cloudModels.providers[newProvider];
    const list = providerEntry?.models ?? [];
    const recommended = list.find((m) => m.recommended) ?? list[0];

    if (!recommended) {
      // Registry hasn't loaded yet or the provider has no models — just flip
      // the provider; user can fill in models manually.
      setSettings({ ...settings, llm: { ...settings.llm, chat_provider: newProvider } });
      return;
    }

    const visionFallback = recommended.supports_vision
      ? recommended.id
      : (list.find((m) => m.supports_vision)?.id ?? recommended.id);

    const llmPatch: Partial<Settings['llm']> = { chat_provider: newProvider };
    if (newProvider === 'openai') {
      llmPatch.openai_chat_model = recommended.id;
      llmPatch.openai_extraction_model = recommended.id;
      llmPatch.openai_vision_model = visionFallback;
    } else if (newProvider === 'anthropic') {
      llmPatch.anthropic_chat_model = recommended.id;
      llmPatch.anthropic_extraction_model = recommended.id;
      llmPatch.anthropic_vision_model = visionFallback;
    } else if (newProvider === 'gemini') {
      llmPatch.gemini_chat_model = recommended.id;
      llmPatch.gemini_extraction_model = recommended.id;
      llmPatch.gemini_vision_model = visionFallback;
    }

    setSettings({ ...settings, llm: { ...settings.llm, ...llmPatch } });
  };

  return {
    newInstance,
    setNewInstance,
    presets,
    applyingPreset,
    presetMessage,
    setPresetMessage,
    showAdvanced,
    setShowAdvanced,
    verifyingUrl,
    urlVerification,
    clearUrlVerification,
    cloudModels,
    ollamaInstances,
    enabledInstanceCount,
    primaryOllamaUrl,
    currentPreset,
    handleApplyPreset,
    handleVerifyOllamaUrl,
    handleUrlChange,
    handleAddInstance,
    handleRemoveInstance,
    handleToggleInstance,
    handleChatProviderChange,
  };
}
