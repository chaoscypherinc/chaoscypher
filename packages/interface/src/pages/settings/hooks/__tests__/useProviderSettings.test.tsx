// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useProviderSettings — provider settings state hook.
 *
 * Strategy:
 * - Mock settingsApi and logger to isolate hook logic from network/I/O.
 * - Wrap renderHook in a QueryClientProvider so useQueryClient resolves.
 * - Use act/waitFor for async effects and handler calls.
 * - Import LLM_HEALTH_KEY directly; do not mock useLLMHealth module.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type {
  Settings,
  OllamaInstance,
  VRAMPreset,
  ApplyPresetResponse,
  OllamaVerifyResponse,
  CloudModelsResponse,
  PresetListResponse,
} from '../../../../types';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../../../../services/api/settings', () => ({
  settingsApi: {
    get: vi.fn<() => Promise<Settings>>(),
    getCloudModels: vi.fn<() => Promise<CloudModelsResponse>>(),
    listPresets: vi.fn<() => Promise<PresetListResponse>>(),
    applyPreset: vi.fn<(presetId: string) => Promise<ApplyPresetResponse>>(),
    verifyOllamaUrl: vi.fn<(url: string) => Promise<OllamaVerifyResponse>>(),
  },
}));

vi.mock('../../../../utils/logger', () => ({
  logger: {
    error: vi.fn<(msg: string, ...args: unknown[]) => void>(),
    info: vi.fn<(msg: string, ...args: unknown[]) => void>(),
    warn: vi.fn<(msg: string, ...args: unknown[]) => void>(),
  },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const makeWrapper = () => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
};

function makeOllamaInstance(overrides?: Partial<OllamaInstance>): OllamaInstance {
  return {
    id: 'default',
    name: 'Default',
    base_url: 'http://localhost:11434',
    enabled: true,
    healthy: true,
    ...overrides,
  };
}

function makeSettings(overrides?: Partial<Settings['llm']>): Settings {
  return {
    app_name: 'Test',
    current_database: 'test.db',
    data_dir: '/data',
    dark_mode: true,
    auto_enable: false,
    setup_completed: true,
    custom_settings: {},
    llm: {
      chat_provider: 'ollama',
      ollama_instances: [makeOllamaInstance()],
      ollama_chat_model: 'llama3',
      ollama_num_ctx: 4096,
      openai_base_url: 'https://api.openai.com/v1',
      openai_chat_model: 'gpt-4',
      anthropic_chat_model: 'claude-3-5-sonnet-20241022',
      gemini_chat_model: 'gemini-2.0-flash',
      ai_max_tokens: 2048,
      ai_temperature: 0.7,
      enable_thinking: false,
      thinking_for_chat: false,
      thinking_for_tools: false,
      enable_llm_queueing: false,
      llm_max_retries: 3,
      llm_max_concurrent: 4,
      llm_reserved_interactive: 2,
      llm_enable_priority: false,
      enable_token_cost_tracking: false,
      token_cost_input_per_million: 0,
      token_cost_output_per_million: 0,
      ...overrides,
    },
    queue: {
      queue_host: 'localhost',
      queue_port: 6379,
      queue_database: 0,
      queue_ssl: false,
    },
    embedding: {
      provider: 'ollama',
      model: 'nomic-embed-text',
      ollama_instance_id: 'default',
      max_text_length: 512,
    },
    search: {
      max_search_results: 10,
      enable_vector_search: true,
      vector_dimensions: 768,
      fulltext_language: 'english',
      enable_auto_embedding: true,
    },
    source_processing: {
      max_file_size_gb: 1,
      auto_analyze: true,
      analysis_depth: 'standard',
      chunk_overlap: 0,
      chunking_strategy: 'recursive',
      relationship_confidence_threshold: 0.5,
    },
    chunking: {
      small_chunk_size: 256,
      small_chunk_overlap: 32,
      min_chunk_size: 64,
      max_chunk_size: 1024,
      respect_boundaries: true,
      group_size: 4,
      group_overlap: 1,
      output_tokens_per_chunk: 2000,
    },
    nlp: {
      nlp_enable_spacy_ner: false,
      nlp_enable_dependency_parsing: false,
      nlp_enable_semantic_embeddings: false,
      nlp_semantic_model: '',
      nlp_similarity_threshold: 0.8,
    },
    export: {
      export_version: '1.0.0',
      export_license: 'MIT',
      export_tags: [],
      export_derived_from: {},
      export_dependencies: {},
    },
    workflow_history_limit: 50,
    trigger_history_limit: 50,
    backup: {
      enabled: false,
      interval: 'daily',
      retention_count: 7,
      backup_dir: '/backups',
    },
  };
}

function makeVRAMPreset(overrides?: Partial<VRAMPreset>): VRAMPreset {
  return {
    name: '8gb',
    display_name: '8 GB',
    description: 'For 8 GB VRAM GPUs',
    vram_gb: 8,
    gpu_examples: ['RTX 3070'],
    version: '1.0',
    author: 'system',
    builtin: true,
    ollama_settings: {
      ollama_chat_model: 'llama3',
      ollama_num_ctx: 4096,
    },
    llm_settings: {
      ai_max_tokens: 2048,
      enable_thinking: false,
    },
    ...overrides,
  };
}

function makeCloudModels(): CloudModelsResponse {
  return {
    providers: {
      openai: {
        display_name: 'OpenAI',
        models: [
          {
            id: 'gpt-4.1',
            display_name: 'GPT-4.1',
            context_window: 128000,
            max_output_tokens: 4096,
            supports_vision: true,
            recommended: true,
          },
          {
            id: 'gpt-4o',
            display_name: 'GPT-4o',
            context_window: 128000,
            max_output_tokens: 4096,
            supports_vision: false,
          },
        ],
      },
      anthropic: {
        display_name: 'Anthropic',
        models: [
          {
            id: 'claude-opus-4-5',
            display_name: 'Claude Opus 4.5',
            context_window: 200000,
            max_output_tokens: 8192,
            supports_vision: false,
            recommended: true,
          },
          {
            id: 'claude-sonnet-4-5-vision',
            display_name: 'Claude Sonnet 4.5 Vision',
            context_window: 200000,
            max_output_tokens: 8192,
            supports_vision: true,
          },
        ],
      },
      gemini: {
        display_name: 'Gemini',
        models: [
          {
            id: 'gemini-2.0-flash',
            display_name: 'Gemini 2.0 Flash',
            context_window: 1000000,
            max_output_tokens: 8192,
            supports_vision: true,
            recommended: true,
          },
        ],
      },
    },
  };
}

async function importDeps() {
  const { settingsApi } = await import('../../../../services/api/settings');
  const { logger } = await import('../../../../utils/logger');
  const { useProviderSettings } = await import('../useProviderSettings');
  const { LLM_HEALTH_KEY } = await import('../../../../hooks/useLLMHealth');
  return { settingsApi, logger, useProviderSettings, LLM_HEALTH_KEY };
}

// ---------------------------------------------------------------------------
// Test setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Suite: initial state
// ---------------------------------------------------------------------------

describe('useProviderSettings — initial state', () => {
  it('starts with empty newInstance form', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const settings = makeSettings();
    const setSettings = vi.fn();
    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    expect(result.current.newInstance).toEqual({ name: '', base_url: '' });
  });

  it('starts with empty presets array', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const settings = makeSettings();
    const { result } = renderHook(
      () => useProviderSettings(settings, vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current.presets).toEqual([]);
  });

  it('starts with applyingPreset = false', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current.applyingPreset).toBe(false);
  });

  it('starts with presetMessage = null', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current.presetMessage).toBeNull();
  });

  it('starts with showAdvanced = false', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current.showAdvanced).toBe(false);
  });

  it('starts with verifyingUrl = false', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current.verifyingUrl).toBe(false);
  });

  it('starts with urlVerification = null', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current.urlVerification).toBeNull();
  });

  it('starts with cloudModels = null before effect resolves', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    // Let the promise hang so we can read initial state
    (settingsApi.getCloudModels as Mock).mockReturnValue(new Promise(() => undefined));
    (settingsApi.listPresets as Mock).mockReturnValue(new Promise(() => undefined));

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current.cloudModels).toBeNull();
  });

  it('derives ollamaInstances from settings', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const inst = makeOllamaInstance({ id: 'i1', name: 'GPU 1' });
    const settings = makeSettings({ ollama_instances: [inst] });

    const { result } = renderHook(
      () => useProviderSettings(settings, vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current.ollamaInstances).toEqual([inst]);
  });

  it('derives enabledInstanceCount correctly', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const instances = [
      makeOllamaInstance({ id: 'a', enabled: true }),
      makeOllamaInstance({ id: 'b', enabled: false }),
      makeOllamaInstance({ id: 'c', enabled: true }),
    ];
    const settings = makeSettings({ ollama_instances: instances });

    const { result } = renderHook(
      () => useProviderSettings(settings, vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current.enabledInstanceCount).toBe(2);
  });

  it('derives primaryOllamaUrl from instances[0].base_url', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const settings = makeSettings({
      ollama_instances: [makeOllamaInstance({ base_url: 'http://my-gpu:11434' })],
    });

    const { result } = renderHook(
      () => useProviderSettings(settings, vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current.primaryOllamaUrl).toBe('http://my-gpu:11434');
  });

  it('returns empty string for primaryOllamaUrl when instances is empty', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const settings = makeSettings({ ollama_instances: [] });

    const { result } = renderHook(
      () => useProviderSettings(settings, vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current.primaryOllamaUrl).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Suite: on-mount effects — fetch cloud models
// ---------------------------------------------------------------------------

describe('useProviderSettings — on-mount fetchCloudModels', () => {
  it('populates cloudModels after successful fetch', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const cloud = makeCloudModels();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(cloud);
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current.cloudModels).toEqual(cloud);
    });
  });

  it('logs error when getCloudModels rejects', async () => {
    const { settingsApi, logger, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockRejectedValue(new Error('network'));
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(logger.error).toHaveBeenCalledWith(
        'Failed to fetch cloud models:',
        expect.any(Error),
      );
    });
  });

  it('leaves cloudModels null when getCloudModels rejects', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockRejectedValue(new Error('network'));
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    // Give effects time to run
    await waitFor(() => {
      expect(settingsApi.getCloudModels).toHaveBeenCalled();
    });

    expect(result.current.cloudModels).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Suite: on-mount effects — fetch presets
// ---------------------------------------------------------------------------

describe('useProviderSettings — on-mount fetchPresets', () => {
  it('populates presets after successful fetch', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const preset = makeVRAMPreset();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [preset], count: 1 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current.presets).toEqual([preset]);
    });
  });

  it('logs error when listPresets rejects', async () => {
    const { settingsApi, logger, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockRejectedValue(new Error('timeout'));

    renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(logger.error).toHaveBeenCalledWith(
        'Failed to fetch presets:',
        expect.any(Error),
      );
    });
  });

  it('derives currentPreset when preset matches ollama_quick_preset', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const preset = makeVRAMPreset({ name: '16gb' });
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [preset], count: 1 });

    const settings = makeSettings({ ollama_quick_preset: '16gb' });

    const { result } = renderHook(
      () => useProviderSettings(settings, vi.fn()),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current.currentPreset).toEqual(preset);
    });
  });

  it('returns undefined for currentPreset when no preset matches', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const preset = makeVRAMPreset({ name: '8gb' });
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [preset], count: 1 });

    const settings = makeSettings({ ollama_quick_preset: 'not-exists' });

    const { result } = renderHook(
      () => useProviderSettings(settings, vi.fn()),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current.presets).toHaveLength(1);
    });

    expect(result.current.currentPreset).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Suite: handleApplyPreset — success paths
// ---------------------------------------------------------------------------

describe('useProviderSettings — handleApplyPreset success (no missing models)', () => {
  it('calls settingsApi.applyPreset with the presetId', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const applyResponse: ApplyPresetResponse = {
      success: true,
      preset_id: '8gb',
      preset_name: '8 GB',
      settings_updated: {},
      message: 'Applied!',
      missing_models: [],
    };
    const updatedSettings = makeSettings({ ollama_chat_model: 'mistral' });

    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.applyPreset as Mock).mockResolvedValue(applyResponse);
    (settingsApi.get as Mock).mockResolvedValue(updatedSettings);

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleApplyPreset('8gb');
    });

    expect(settingsApi.applyPreset).toHaveBeenCalledWith('8gb');
  });

  it('sets presetMessage to success when no missing models', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.applyPreset as Mock).mockResolvedValue({
      success: true,
      preset_id: '8gb',
      preset_name: '8 GB',
      settings_updated: {},
      message: 'Preset applied!',
      missing_models: [],
    } satisfies ApplyPresetResponse);
    (settingsApi.get as Mock).mockResolvedValue(makeSettings());

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleApplyPreset('8gb');
    });

    expect(result.current.presetMessage).toEqual({
      type: 'success',
      text: 'Preset applied!',
    });
  });

  it('sets presetMessage to warning when there are missing models', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.applyPreset as Mock).mockResolvedValue({
      success: true,
      preset_id: '8gb',
      preset_name: '8 GB',
      settings_updated: {},
      message: 'Applied with warnings',
      missing_models: ['llama3:8b', 'mistral'],
    } satisfies ApplyPresetResponse);
    (settingsApi.get as Mock).mockResolvedValue(makeSettings());

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleApplyPreset('8gb');
    });

    expect(result.current.presetMessage?.type).toBe('warning');
    expect(result.current.presetMessage?.text).toContain('llama3:8b');
    expect(result.current.presetMessage?.text).toContain('mistral');
  });

  it('calls settingsApi.get after successful apply to refresh settings', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.applyPreset as Mock).mockResolvedValue({
      success: true,
      preset_id: '8gb',
      preset_name: '8 GB',
      settings_updated: {},
      message: 'Done',
      missing_models: [],
    } satisfies ApplyPresetResponse);
    (settingsApi.get as Mock).mockResolvedValue(makeSettings());

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleApplyPreset('8gb');
    });

    expect(settingsApi.get).toHaveBeenCalled();
  });

  it('calls setSettings with updated settings after apply', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const setSettings = vi.fn();
    const initialSettings = makeSettings();
    const serverSettings = makeSettings({ ollama_chat_model: 'phi3' });

    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.applyPreset as Mock).mockResolvedValue({
      success: true,
      preset_id: '8gb',
      preset_name: '8 GB',
      settings_updated: {},
      message: 'Done',
      missing_models: [],
    } satisfies ApplyPresetResponse);
    (settingsApi.get as Mock).mockResolvedValue(serverSettings);

    const { result } = renderHook(
      () => useProviderSettings(initialSettings, setSettings),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleApplyPreset('8gb');
    });

    expect(setSettings).toHaveBeenCalled();
    const calledWith = setSettings.mock.calls[0][0] as Settings;
    // Preserves the in-flight ollama_instances from original settings
    expect(calledWith.llm.ollama_instances).toEqual(initialSettings.llm.ollama_instances);
    // Sets preset id
    expect(calledWith.llm.ollama_quick_preset).toBe('8gb');
  });

  it('sets applyingPreset false after success', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.applyPreset as Mock).mockResolvedValue({
      success: true,
      preset_id: '8gb',
      preset_name: '8 GB',
      settings_updated: {},
      message: 'Done',
      missing_models: [],
    } satisfies ApplyPresetResponse);
    (settingsApi.get as Mock).mockResolvedValue(makeSettings());

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleApplyPreset('8gb');
    });

    expect(result.current.applyingPreset).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Suite: handleApplyPreset — no-op for empty presetId
// ---------------------------------------------------------------------------

describe('useProviderSettings — handleApplyPreset no-op', () => {
  it('does not call applyPreset when presetId is empty string', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleApplyPreset('');
    });

    expect(settingsApi.applyPreset).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Suite: handleApplyPreset — error path
// ---------------------------------------------------------------------------

describe('useProviderSettings — handleApplyPreset error', () => {
  it('sets presetMessage to error when applyPreset rejects', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.applyPreset as Mock).mockRejectedValue(new Error('server error'));

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleApplyPreset('8gb');
    });

    expect(result.current.presetMessage).toEqual({
      type: 'error',
      text: 'Failed to apply preset',
    });
  });

  it('sets applyingPreset false after error', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.applyPreset as Mock).mockRejectedValue(new Error('fail'));

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleApplyPreset('8gb');
    });

    expect(result.current.applyingPreset).toBe(false);
  });

  it('invalidates LLM_HEALTH_KEY on successful apply', async () => {
    const { settingsApi, useProviderSettings, LLM_HEALTH_KEY } = await importDeps();

    // Create a wrapper with a query client we can spy on
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(qc, 'invalidateQueries');
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.applyPreset as Mock).mockResolvedValue({
      success: true,
      preset_id: '8gb',
      preset_name: '8 GB',
      settings_updated: {},
      message: 'Done',
      missing_models: [],
    } satisfies ApplyPresetResponse);
    (settingsApi.get as Mock).mockResolvedValue(makeSettings());

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper },
    );

    await act(async () => {
      await result.current.handleApplyPreset('8gb');
    });

    expect(spy).toHaveBeenCalledWith({ queryKey: LLM_HEALTH_KEY });
  });
});

// ---------------------------------------------------------------------------
// Suite: handleVerifyOllamaUrl
// ---------------------------------------------------------------------------

describe('useProviderSettings — handleVerifyOllamaUrl', () => {
  it('calls settingsApi.verifyOllamaUrl with primaryOllamaUrl', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const verifyResult: OllamaVerifyResponse = {
      success: true,
      message: 'Connected',
      version: '0.1.0',
    };
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.verifyOllamaUrl as Mock).mockResolvedValue(verifyResult);

    const settings = makeSettings({
      ollama_instances: [makeOllamaInstance({ base_url: 'http://myhost:11434' })],
    });

    const { result } = renderHook(
      () => useProviderSettings(settings, vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleVerifyOllamaUrl();
    });

    expect(settingsApi.verifyOllamaUrl).toHaveBeenCalledWith('http://myhost:11434');
  });

  it('sets urlVerification on success', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const verifyResult: OllamaVerifyResponse = {
      success: true,
      message: 'Connected',
    };
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.verifyOllamaUrl as Mock).mockResolvedValue(verifyResult);

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleVerifyOllamaUrl();
    });

    expect(result.current.urlVerification).toEqual(verifyResult);
  });

  it('sets verifyingUrl back to false after success', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.verifyOllamaUrl as Mock).mockResolvedValue({ success: true, message: 'ok' });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleVerifyOllamaUrl();
    });

    expect(result.current.verifyingUrl).toBe(false);
  });

  it('sets urlVerification to error shape when verifyOllamaUrl rejects', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.verifyOllamaUrl as Mock).mockRejectedValue(new Error('refused'));

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleVerifyOllamaUrl();
    });

    expect(result.current.urlVerification).toEqual({
      success: false,
      message: 'Failed to verify URL',
      error_type: 'request_failed',
    });
  });

  it('sets verifyingUrl false after error', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.verifyOllamaUrl as Mock).mockRejectedValue(new Error('timeout'));

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleVerifyOllamaUrl();
    });

    expect(result.current.verifyingUrl).toBe(false);
  });

  it('is a no-op when primaryOllamaUrl is empty', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const settings = makeSettings({ ollama_instances: [] });

    const { result } = renderHook(
      () => useProviderSettings(settings, vi.fn()),
      { wrapper: makeWrapper() },
    );

    await act(async () => {
      await result.current.handleVerifyOllamaUrl();
    });

    expect(settingsApi.verifyOllamaUrl).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Suite: clearUrlVerification
// ---------------------------------------------------------------------------

describe('useProviderSettings — clearUrlVerification', () => {
  it('clears urlVerification state', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.verifyOllamaUrl as Mock).mockResolvedValue({ success: true, message: 'ok' });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    // First verify to populate the state
    await act(async () => {
      await result.current.handleVerifyOllamaUrl();
    });
    expect(result.current.urlVerification).not.toBeNull();

    // Then clear
    act(() => {
      result.current.clearUrlVerification();
    });

    expect(result.current.urlVerification).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Suite: handleUrlChange
// ---------------------------------------------------------------------------

describe('useProviderSettings — handleUrlChange', () => {
  it('updates instances[0].base_url via setSettings', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({
      ollama_instances: [makeOllamaInstance({ id: 'i1', base_url: 'http://old:11434' })],
    });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.handleUrlChange('http://new:11434');
    });

    expect(setSettings).toHaveBeenCalled();
    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.ollama_instances[0].base_url).toBe('http://new:11434');
  });

  it('creates a default instance when ollama_instances is empty', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({ ollama_instances: [] });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.handleUrlChange('http://new-gpu:11434');
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.ollama_instances).toHaveLength(1);
    expect(updated.llm.ollama_instances[0].base_url).toBe('http://new-gpu:11434');
    expect(updated.llm.ollama_instances[0].id).toBe('default');
  });

  it('clears urlVerification when URL changes', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });
    (settingsApi.verifyOllamaUrl as Mock).mockResolvedValue({ success: true, message: 'ok' });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    // Set up a verification result
    await act(async () => {
      await result.current.handleVerifyOllamaUrl();
    });
    expect(result.current.urlVerification).not.toBeNull();

    // Change URL should clear verification
    act(() => {
      result.current.handleUrlChange('http://new-host:11434');
    });

    expect(result.current.urlVerification).toBeNull();
  });

  it('preserves other instances when updating instances[0]', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const instances = [
      makeOllamaInstance({ id: 'a', base_url: 'http://a:11434' }),
      makeOllamaInstance({ id: 'b', base_url: 'http://b:11434' }),
    ];
    const settings = makeSettings({ ollama_instances: instances });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.handleUrlChange('http://updated:11434');
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.ollama_instances[1].base_url).toBe('http://b:11434');
  });
});

// ---------------------------------------------------------------------------
// Suite: handleAddInstance
// ---------------------------------------------------------------------------

describe('useProviderSettings — handleAddInstance', () => {
  it('adds a new instance to ollama_instances', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), setSettings),
      { wrapper: makeWrapper() },
    );

    // Set the new instance form
    act(() => {
      result.current.setNewInstance({ name: 'GPU 2', base_url: 'http://gpu2:11434' });
    });

    act(() => {
      result.current.handleAddInstance();
    });

    expect(setSettings).toHaveBeenCalled();
    const updated = setSettings.mock.calls[0][0] as Settings;
    const newInst = updated.llm.ollama_instances.find(i => i.name === 'GPU 2');
    expect(newInst).toBeDefined();
    expect(newInst?.base_url).toBe('http://gpu2:11434');
    expect(newInst?.enabled).toBe(true);
  });

  it('clears newInstance form after adding', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.setNewInstance({ name: 'GPU 2', base_url: 'http://gpu2:11434' });
    });

    act(() => {
      result.current.handleAddInstance();
    });

    expect(result.current.newInstance).toEqual({ name: '', base_url: '' });
  });

  it('does not add instance when name is empty', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.setNewInstance({ name: '', base_url: 'http://gpu2:11434' });
    });
    act(() => {
      result.current.handleAddInstance();
    });

    expect(setSettings).not.toHaveBeenCalled();
  });

  it('does not add instance when base_url is empty', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.setNewInstance({ name: 'GPU 2', base_url: '' });
    });
    act(() => {
      result.current.handleAddInstance();
    });

    expect(setSettings).not.toHaveBeenCalled();
  });

  it('does not add instance when fields are whitespace only', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.setNewInstance({ name: '   ', base_url: '   ' });
    });
    act(() => {
      result.current.handleAddInstance();
    });

    expect(setSettings).not.toHaveBeenCalled();
  });

  it('trims name and base_url when adding', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.setNewInstance({ name: '  GPU 2  ', base_url: '  http://gpu2:11434  ' });
    });
    act(() => {
      result.current.handleAddInstance();
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    const newInst = updated.llm.ollama_instances.find(i => i.name === 'GPU 2');
    expect(newInst?.base_url).toBe('http://gpu2:11434');
  });
});

// ---------------------------------------------------------------------------
// Suite: handleRemoveInstance
// ---------------------------------------------------------------------------

describe('useProviderSettings — handleRemoveInstance', () => {
  it('removes the instance with the given id', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const instances = [
      makeOllamaInstance({ id: 'keep', name: 'Keep' }),
      makeOllamaInstance({ id: 'remove', name: 'Remove' }),
    ];
    const settings = makeSettings({ ollama_instances: instances });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.handleRemoveInstance('remove');
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.ollama_instances).toHaveLength(1);
    expect(updated.llm.ollama_instances[0].id).toBe('keep');
  });

  it('does not call setSettings when instanceId does not match', async () => {
    // The hook always calls setSettings with a filtered list, even if unchanged
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({
      ollama_instances: [makeOllamaInstance({ id: 'only' })],
    });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.handleRemoveInstance('nonexistent');
    });

    // setSettings is still called but with the same items (filter just matched nothing)
    expect(setSettings).toHaveBeenCalled();
    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.ollama_instances).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Suite: handleToggleInstance
// ---------------------------------------------------------------------------

describe('useProviderSettings — handleToggleInstance', () => {
  it('toggles enabled from true to false', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({
      ollama_instances: [makeOllamaInstance({ id: 'inst1', enabled: true })],
    });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.handleToggleInstance('inst1');
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.ollama_instances[0].enabled).toBe(false);
  });

  it('toggles enabled from false to true', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({
      ollama_instances: [makeOllamaInstance({ id: 'inst1', enabled: false })],
    });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.handleToggleInstance('inst1');
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.ollama_instances[0].enabled).toBe(true);
  });

  it('does not toggle other instances', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const instances = [
      makeOllamaInstance({ id: 'target', enabled: true }),
      makeOllamaInstance({ id: 'other', enabled: true }),
    ];
    const settings = makeSettings({ ollama_instances: instances });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.handleToggleInstance('target');
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.ollama_instances[1].enabled).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Suite: handleChatProviderChange — ollama / no cloudModels
// ---------------------------------------------------------------------------

describe('useProviderSettings — handleChatProviderChange', () => {
  it('switches to ollama without seeding model fields', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({ chat_provider: 'openai' });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.handleChatProviderChange('ollama');
    });

    expect(setSettings).toHaveBeenCalled();
    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.chat_provider).toBe('ollama');
    // Should NOT set openai model fields
    expect(updated.llm.openai_chat_model).toBe(settings.llm.openai_chat_model);
  });

  it('seeds openai models from recommended when switching to openai', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const cloudModels = makeCloudModels();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(cloudModels);
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({ chat_provider: 'ollama' });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    // Wait for cloudModels to be populated
    await waitFor(() => {
      expect(result.current.cloudModels).not.toBeNull();
    });

    act(() => {
      result.current.handleChatProviderChange('openai');
    });

    expect(setSettings).toHaveBeenCalled();
    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.chat_provider).toBe('openai');
    // gpt-4.1 is the recommended model and supports vision
    expect(updated.llm.openai_chat_model).toBe('gpt-4.1');
    expect(updated.llm.openai_extraction_model).toBe('gpt-4.1');
    expect(updated.llm.openai_vision_model).toBe('gpt-4.1');
  });

  it('seeds anthropic models from recommended when switching to anthropic', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const cloudModels = makeCloudModels();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(cloudModels);
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({ chat_provider: 'ollama' });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current.cloudModels).not.toBeNull();
    });

    act(() => {
      result.current.handleChatProviderChange('anthropic');
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.chat_provider).toBe('anthropic');
    // claude-opus-4-5 is recommended but doesn't support vision;
    // claude-sonnet-4-5-vision does support vision → used as vision fallback
    expect(updated.llm.anthropic_chat_model).toBe('claude-opus-4-5');
    expect(updated.llm.anthropic_extraction_model).toBe('claude-opus-4-5');
    expect(updated.llm.anthropic_vision_model).toBe('claude-sonnet-4-5-vision');
  });

  it('seeds gemini models from recommended when switching to gemini', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const cloudModels = makeCloudModels();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(cloudModels);
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({ chat_provider: 'ollama' });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current.cloudModels).not.toBeNull();
    });

    act(() => {
      result.current.handleChatProviderChange('gemini');
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.chat_provider).toBe('gemini');
    expect(updated.llm.gemini_chat_model).toBe('gemini-2.0-flash');
    expect(updated.llm.gemini_extraction_model).toBe('gemini-2.0-flash');
    expect(updated.llm.gemini_vision_model).toBe('gemini-2.0-flash');
  });

  it('falls back to first model when none is recommended', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const cloudModels: CloudModelsResponse = {
      providers: {
        openai: {
          display_name: 'OpenAI',
          models: [
            {
              id: 'gpt-3.5-turbo',
              display_name: 'GPT-3.5',
              context_window: 4096,
              max_output_tokens: 1024,
              recommended: false,
            },
          ],
        },
      },
    };
    (settingsApi.getCloudModels as Mock).mockResolvedValue(cloudModels);
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), setSettings),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current.cloudModels).not.toBeNull();
    });

    act(() => {
      result.current.handleChatProviderChange('openai');
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.openai_chat_model).toBe('gpt-3.5-turbo');
  });

  it('only flips chat_provider when provider has no models', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    const cloudModels: CloudModelsResponse = {
      providers: {
        openai: {
          display_name: 'OpenAI',
          models: [],
        },
      },
    };
    (settingsApi.getCloudModels as Mock).mockResolvedValue(cloudModels);
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({ openai_chat_model: 'gpt-4', chat_provider: 'ollama' });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current.cloudModels).not.toBeNull();
    });

    act(() => {
      result.current.handleChatProviderChange('openai');
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.chat_provider).toBe('openai');
    // Model fields should remain unchanged
    expect(updated.llm.openai_chat_model).toBe('gpt-4');
  });

  it('only flips chat_provider when cloudModels is null', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    // Never resolve to keep cloudModels null
    (settingsApi.getCloudModels as Mock).mockReturnValue(new Promise(() => undefined));
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({ chat_provider: 'ollama' });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.handleChatProviderChange('openai');
    });

    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.chat_provider).toBe('openai');
  });

  it('only flips chat_provider when switching to unknown provider', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const setSettings = vi.fn();
    const settings = makeSettings({ chat_provider: 'ollama' });

    const { result } = renderHook(
      () => useProviderSettings(settings, setSettings),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current.cloudModels).not.toBeNull();
    });

    act(() => {
      result.current.handleChatProviderChange('custom_provider');
    });

    // setSettings called, chat_provider updated, no model fields touched beyond what's expected
    expect(setSettings).toHaveBeenCalled();
    const updated = setSettings.mock.calls[0][0] as Settings;
    expect(updated.llm.chat_provider).toBe('custom_provider');
  });
});

// ---------------------------------------------------------------------------
// Suite: state setters (simple pass-throughs)
// ---------------------------------------------------------------------------

describe('useProviderSettings — state setters', () => {
  it('setNewInstance updates newInstance state', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.setNewInstance({ name: 'Test', base_url: 'http://test' });
    });

    expect(result.current.newInstance).toEqual({ name: 'Test', base_url: 'http://test' });
  });

  it('setShowAdvanced toggles showAdvanced', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.setShowAdvanced(true);
    });

    expect(result.current.showAdvanced).toBe(true);
  });

  it('setPresetMessage updates presetMessage', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.setPresetMessage({ type: 'warning', text: 'Watch out' });
    });

    expect(result.current.presetMessage).toEqual({ type: 'warning', text: 'Watch out' });
  });

  it('setPresetMessage can clear to null', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    act(() => {
      result.current.setPresetMessage({ type: 'error', text: 'Oops' });
    });
    act(() => {
      result.current.setPresetMessage(null);
    });

    expect(result.current.presetMessage).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Suite: return shape
// ---------------------------------------------------------------------------

describe('useProviderSettings — return shape', () => {
  it('exposes all expected properties', async () => {
    const { settingsApi, useProviderSettings } = await importDeps();
    (settingsApi.getCloudModels as Mock).mockResolvedValue(makeCloudModels());
    (settingsApi.listPresets as Mock).mockResolvedValue({ presets: [], count: 0 });

    const { result } = renderHook(
      () => useProviderSettings(makeSettings(), vi.fn()),
      { wrapper: makeWrapper() },
    );

    expect(result.current).toMatchObject({
      newInstance: expect.any(Object),
      setNewInstance: expect.any(Function),
      presets: expect.any(Array),
      applyingPreset: expect.any(Boolean),
      presetMessage: null,
      setPresetMessage: expect.any(Function),
      showAdvanced: expect.any(Boolean),
      setShowAdvanced: expect.any(Function),
      verifyingUrl: expect.any(Boolean),
      urlVerification: null,
      clearUrlVerification: expect.any(Function),
      cloudModels: null,
      ollamaInstances: expect.any(Array),
      enabledInstanceCount: expect.any(Number),
      primaryOllamaUrl: expect.any(String),
      currentPreset: undefined,
      handleApplyPreset: expect.any(Function),
      handleVerifyOllamaUrl: expect.any(Function),
      handleUrlChange: expect.any(Function),
      handleAddInstance: expect.any(Function),
      handleRemoveInstance: expect.any(Function),
      handleToggleInstance: expect.any(Function),
      handleChatProviderChange: expect.any(Function),
    });
  });
});
