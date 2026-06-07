// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for EmbeddingProviderConfig.tsx
 *
 * Strategy: mock EmbeddingModelSelector, OllamaUrlField, useEmbeddingModels,
 * and settingsApi so each test can inspect the props/payloads produced by the
 * component's handlers without rendering MUI's full Autocomplete machinery.
 * Callbacks are triggered directly on captured mock props.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { Settings, OllamaInstance, OllamaVerifyResponse } from '../../../../types';

// ---------------------------------------------------------------------------
// Mocks — declare before imports so vi.mock hoisting works.
// ---------------------------------------------------------------------------

// Captured props for EmbeddingModelSelector
let embeddingSelectorProps: Record<string, unknown> = {};

vi.mock('../EmbeddingModelSelector', () => ({
  default: (props: Record<string, unknown>) => {
    embeddingSelectorProps = props;
    return <div data-testid="embedding-model-selector" />;
  },
}));

// Captured props for OllamaUrlField
let ollamaUrlFieldProps: Record<string, unknown> = {};

vi.mock('../../../../components/settings', () => ({
  OllamaUrlField: (props: Record<string, unknown>) => {
    ollamaUrlFieldProps = props;
    return (
      <div data-testid="ollama-url-field">
        <input
          data-testid="ollama-url-input"
          value={props.url as string}
          onChange={(e) => (props.onChange as (v: string) => void)(e.target.value)}
        />
        <button
          data-testid="ollama-verify-btn"
          onClick={() => (props.onVerify as () => void)()}
        >
          Verify
        </button>
        <button
          data-testid="ollama-clear-btn"
          onClick={() => (props.onClearVerification as () => void)()}
        >
          Clear
        </button>
      </div>
    );
  },
}));

// Registry returned by useEmbeddingModels
import type { CuratedEmbeddingModel, CloudEmbeddingModel } from '../../hooks/useEmbeddingModels';

interface MockRegistry {
  curated: CuratedEmbeddingModel[];
  cloud: Record<string, CloudEmbeddingModel[]>;
}

let mockRegistry: MockRegistry | null = {
  curated: [
    {
      name: 'nomic-embed',
      local: 'nomic-ai/nomic-embed-text-v1.5',
      ollama: 'nomic-embed-text',
      dimensions: 768,
      mrl: true,
      default: true,
    },
  ],
  cloud: {
    openai: [
      {
        name: 'text-embedding-3-small',
        model: 'text-embedding-3-small',
        dimensions: 1536,
        mrl: true,
        current: true,
      },
    ],
    gemini: [
      {
        name: 'embedding-001',
        model: 'models/embedding-001',
        dimensions: 768,
        mrl: false,
        current: true,
      },
    ],
  },
};

vi.mock('../../hooks/useEmbeddingModels', () => ({
  useEmbeddingModels: () => mockRegistry,
}));

// settingsApi.verifyOllamaUrl
const mockVerifyOllamaUrl = vi.fn<(url: string) => Promise<OllamaVerifyResponse>>();

vi.mock('../../../../services/api/settings', () => ({
  settingsApi: {
    verifyOllamaUrl: (...args: [string]) => mockVerifyOllamaUrl(...args),
  },
}));

// ---------------------------------------------------------------------------
// Component under test (imported AFTER mocks)
// ---------------------------------------------------------------------------
import EmbeddingProviderConfig from '../EmbeddingProviderConfig';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeOllamaInstance(overrides: Partial<OllamaInstance> = {}): OllamaInstance {
  return {
    id: 'primary',
    name: 'Primary',
    base_url: 'http://localhost:11434',
    enabled: true,
    healthy: true,
    ...overrides,
  };
}

function makeSettings(
  embeddingOverrides: Partial<Settings['embedding']> = {},
  searchOverrides: Partial<Settings['search']> = {},
  llmOverrides: { ollama_instances?: OllamaInstance[] } = {},
): Settings {
  return {
    embedding: {
      provider: 'local',
      model: 'nomic-ai/nomic-embed-text-v1.5',
      api_key: null,
      api_base: null,
      ollama_instance_id: '',
      max_text_length: 8192,
      ...embeddingOverrides,
    },
    search: {
      max_search_results: 10,
      enable_vector_search: true,
      vector_dimensions: 768,
      fulltext_language: 'english',
      enable_auto_embedding: false,
      ...searchOverrides,
    },
    llm: {
      chat_provider: 'ollama',
      ollama_instances: [makeOllamaInstance()],
      ollama_chat_model: 'llama3',
      openai_chat_model: 'gpt-4o',
      anthropic_chat_model: 'claude-3-5-sonnet',
      gemini_chat_model: 'gemini-1.5-pro',
      ai_max_tokens: 4096,
      ai_temperature: 0.7,
      enable_thinking: false,
      thinking_for_chat: false,
      thinking_for_tools: false,
      thinking_auto_detect: false,
      chat_interactive_streaming: true,
      enable_llm_queueing: false,
      llm_max_retries: 3,
      llm_max_concurrent: 4,
      llm_reserved_interactive: 2,
      llm_enable_priority: false,
      enable_token_cost_tracking: false,
      token_cost_input_per_million: 0,
      token_cost_output_per_million: 0,
      openai_base_url: 'https://api.openai.com/v1',
      ...llmOverrides,
    },
  } as unknown as Settings;
}

beforeEach(() => {
  embeddingSelectorProps = {};
  ollamaUrlFieldProps = {};
  mockVerifyOllamaUrl.mockReset();

  // Reset registry to default non-null value
  mockRegistry = {
    curated: [
      {
        name: 'nomic-embed',
        local: 'nomic-ai/nomic-embed-text-v1.5',
        ollama: 'nomic-embed-text',
        dimensions: 768,
        mrl: true,
        default: true,
      },
    ],
    cloud: {
      openai: [
        {
          name: 'text-embedding-3-small',
          model: 'text-embedding-3-small',
          dimensions: 1536,
          mrl: true,
          current: true,
        },
      ],
      gemini: [
        {
          name: 'embedding-001',
          model: 'models/embedding-001',
          dimensions: 768,
          mrl: false,
          current: true,
        },
      ],
    },
  };
});

// ---------------------------------------------------------------------------
// Rendering + static structure
// ---------------------------------------------------------------------------

describe('EmbeddingProviderConfig — rendering', () => {
  it('renders heading and description text', () => {
    render(<EmbeddingProviderConfig settings={makeSettings()} setSettings={vi.fn()} />);
    expect(screen.getByText('Embedding Provider')).toBeInTheDocument();
    expect(screen.getByText(/Configure the embedding provider/)).toBeInTheDocument();
  });

  it('renders EmbeddingModelSelector with correct initial props for local provider', () => {
    const settings = makeSettings({ provider: 'local', model: 'some-local-model' });
    render(<EmbeddingProviderConfig settings={settings} setSettings={vi.fn()} />);
    expect(screen.getByTestId('embedding-model-selector')).toBeInTheDocument();
    expect(embeddingSelectorProps.provider).toBe('local');
    expect(embeddingSelectorProps.model).toBe('some-local-model');
  });

  it('defaults provider to "local" when embedding.provider is not set', () => {
    const settings = makeSettings({ provider: undefined as unknown as string });
    render(<EmbeddingProviderConfig settings={settings} setSettings={vi.fn()} />);
    expect(embeddingSelectorProps.provider).toBe('local');
  });

  it('hides OllamaUrlField for local provider', () => {
    render(<EmbeddingProviderConfig settings={makeSettings({ provider: 'local' })} setSettings={vi.fn()} />);
    expect(screen.queryByTestId('ollama-url-field')).not.toBeInTheDocument();
  });

  it('shows OllamaUrlField for ollama provider', () => {
    render(<EmbeddingProviderConfig settings={makeSettings({ provider: 'ollama' })} setSettings={vi.fn()} />);
    expect(screen.getByTestId('ollama-url-field')).toBeInTheDocument();
  });

  it('hides cloud API key / base URL fields for local provider', () => {
    render(<EmbeddingProviderConfig settings={makeSettings({ provider: 'local' })} setSettings={vi.fn()} />);
    expect(screen.queryByLabelText('API Key')).not.toBeInTheDocument();
  });

  it('shows cloud API key and base URL fields for openai provider', () => {
    render(<EmbeddingProviderConfig settings={makeSettings({ provider: 'openai' })} setSettings={vi.fn()} />);
    expect(screen.getByLabelText('API Key')).toBeInTheDocument();
    expect(screen.getByLabelText('API Base URL (optional)')).toBeInTheDocument();
  });

  it('shows cloud API key and base URL fields for gemini provider', () => {
    render(<EmbeddingProviderConfig settings={makeSettings({ provider: 'gemini' })} setSettings={vi.fn()} />);
    expect(screen.getByLabelText('API Key')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Helper: open the MUI Select for "Provider" and click an item by text.
// MUI Select renders as a button (combobox) — you open it with mouseDown
// on the labeled div, then click the desired listbox item.
// ---------------------------------------------------------------------------

function selectProvider(label: string) {
  // The MUI Select renders as a div[role="combobox"] associated to a hidden
  // native <select>.  We open it by clicking the visible element.
  fireEvent.mouseDown(screen.getByLabelText(/Provider/i));
  const option = screen.getByRole('option', { name: label });
  fireEvent.click(option);
}

// ---------------------------------------------------------------------------
// Provider Select change → handleProviderChange
// ---------------------------------------------------------------------------

describe('handleProviderChange', () => {
  it('switching to local seeds model from curated default (.local) and dimensions', () => {
    const setSettings = vi.fn();
    const settings = makeSettings({ provider: 'openai', model: 'text-embedding-3-small' });
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    selectProvider('Local (CPU)');

    expect(setSettings).toHaveBeenCalledTimes(1);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.embedding.provider).toBe('local');
    expect(payload.embedding.model).toBe('nomic-ai/nomic-embed-text-v1.5');
    expect(payload.search.vector_dimensions).toBe(768);
  });

  it('switching to ollama seeds model from curated default (.ollama) and dimensions', () => {
    const setSettings = vi.fn();
    const settings = makeSettings({ provider: 'local' });
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    selectProvider('Ollama (GPU)');

    expect(setSettings).toHaveBeenCalledTimes(1);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.embedding.provider).toBe('ollama');
    expect(payload.embedding.model).toBe('nomic-embed-text');
    expect(payload.search.vector_dimensions).toBe(768);
  });

  it('switching to openai seeds model from current cloud model and dimensions', () => {
    const setSettings = vi.fn();
    const settings = makeSettings({ provider: 'local' });
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    selectProvider('OpenAI');

    expect(setSettings).toHaveBeenCalledTimes(1);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.embedding.provider).toBe('openai');
    expect(payload.embedding.model).toBe('text-embedding-3-small');
    expect(payload.search.vector_dimensions).toBe(1536);
  });

  it('switching to gemini seeds from current cloud model', () => {
    const setSettings = vi.fn();
    const settings = makeSettings({ provider: 'local' });
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    selectProvider('Gemini');

    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.embedding.provider).toBe('gemini');
    expect(payload.embedding.model).toBe('models/embedding-001');
    expect(payload.search.vector_dimensions).toBe(768);
  });

  it('fallback when registry is null — clears model, leaves dimensions untouched', () => {
    mockRegistry = null;
    const setSettings = vi.fn();
    const settings = makeSettings({ provider: 'local' }, { vector_dimensions: 512 });
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    selectProvider('OpenAI');

    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.embedding.model).toBe('');
    // dimensions not overwritten when registry is empty
    expect(payload.search.vector_dimensions).toBe(512);
  });

  it('fallback when registry curated is empty for local — clears model, leaves dimensions', () => {
    mockRegistry = { curated: [], cloud: { openai: [], gemini: [] } };
    const setSettings = vi.fn();
    const settings = makeSettings({ provider: 'openai' }, { vector_dimensions: 512 });
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    selectProvider('Local (CPU)');

    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.embedding.model).toBe('');
    expect(payload.search.vector_dimensions).toBe(512);
  });

  it('fallback when registry cloud list is empty for openai — clears model', () => {
    mockRegistry = {
      curated: [],
      cloud: { openai: [], gemini: [] },
    };
    const setSettings = vi.fn();
    const settings = makeSettings({ provider: 'local' });
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    selectProvider('OpenAI');

    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.embedding.model).toBe('');
  });
});

// ---------------------------------------------------------------------------
// EmbeddingModelSelector onModelChange
// ---------------------------------------------------------------------------

describe('EmbeddingModelSelector onModelChange', () => {
  it('updates embedding.model; dimensions provided → also sets vector_dimensions', () => {
    const setSettings = vi.fn();
    const settings = makeSettings({ provider: 'local', model: 'old-model' });
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    const onModelChange = embeddingSelectorProps.onModelChange as (
      model: string,
      dimensions?: number,
    ) => void;
    onModelChange('new-model', 1024);

    expect(setSettings).toHaveBeenCalledTimes(1);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.embedding.model).toBe('new-model');
    expect(payload.search.vector_dimensions).toBe(1024);
  });

  it('updates embedding.model; dimensions omitted → leaves vector_dimensions unchanged', () => {
    const setSettings = vi.fn();
    const settings = makeSettings(
      { provider: 'local', model: 'old-model' },
      { vector_dimensions: 512 },
    );
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    const onModelChange = embeddingSelectorProps.onModelChange as (
      model: string,
      dimensions?: number,
    ) => void;
    onModelChange('typed-model');

    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.embedding.model).toBe('typed-model');
    expect(payload.search.vector_dimensions).toBe(512);
  });
});

// ---------------------------------------------------------------------------
// Dimensions TextField
// ---------------------------------------------------------------------------

describe('Dimensions TextField', () => {
  it('valid integer → sets vector_dimensions via parseInt', () => {
    const setSettings = vi.fn();
    render(<EmbeddingProviderConfig settings={makeSettings()} setSettings={setSettings} />);

    const dimField = screen.getByLabelText('Dimensions');
    fireEvent.change(dimField, { target: { value: '1536' } });

    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.search.vector_dimensions).toBe(1536);
  });

  it('invalid (non-numeric) input → falls back to 768', () => {
    const setSettings = vi.fn();
    render(<EmbeddingProviderConfig settings={makeSettings()} setSettings={setSettings} />);

    const dimField = screen.getByLabelText('Dimensions');
    fireEvent.change(dimField, { target: { value: 'abc' } });

    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.search.vector_dimensions).toBe(768);
  });
});

// ---------------------------------------------------------------------------
// API Key field (cloud providers only)
// ---------------------------------------------------------------------------

describe('API Key field (cloud)', () => {
  it('value is empty string when api_key === "configured"; shows placeholder', () => {
    render(
      <EmbeddingProviderConfig
        settings={makeSettings({ provider: 'openai', api_key: 'configured' })}
        setSettings={vi.fn()}
      />,
    );
    const field = screen.getByLabelText('API Key') as HTMLInputElement;
    expect(field.value).toBe('');
    expect(field.placeholder).toMatch(/API key configured/);
  });

  it('value reflects api_key when not "configured"', () => {
    render(
      <EmbeddingProviderConfig
        settings={makeSettings({ provider: 'openai', api_key: 'sk-abc123' })}
        setSettings={vi.fn()}
      />,
    );
    const field = screen.getByLabelText('API Key') as HTMLInputElement;
    expect(field.value).toBe('sk-abc123');
  });

  it('typing in API Key calls updateEmbedding("api_key", value)', () => {
    const setSettings = vi.fn();
    render(
      <EmbeddingProviderConfig
        settings={makeSettings({ provider: 'openai', api_key: '' })}
        setSettings={setSettings}
      />,
    );
    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'new-key' } });

    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.embedding.api_key).toBe('new-key');
  });

  it('typing in API Base URL calls updateEmbedding("api_base", value)', () => {
    const setSettings = vi.fn();
    render(
      <EmbeddingProviderConfig
        settings={makeSettings({ provider: 'openai', api_base: '' })}
        setSettings={setSettings}
      />,
    );
    fireEvent.change(screen.getByLabelText('API Base URL (optional)'), {
      target: { value: 'https://custom.endpoint.com/v1' },
    });

    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.embedding.api_base).toBe('https://custom.endpoint.com/v1');
  });

  it('helper text mentions OpenAI when provider is openai', () => {
    render(
      <EmbeddingProviderConfig
        settings={makeSettings({ provider: 'openai' })}
        setSettings={vi.fn()}
      />,
    );
    expect(screen.getByText(/OpenAI API key/)).toBeInTheDocument();
  });

  it('helper text mentions Gemini when provider is gemini', () => {
    render(
      <EmbeddingProviderConfig
        settings={makeSettings({ provider: 'gemini' })}
        setSettings={vi.fn()}
      />,
    );
    expect(screen.getByText(/Gemini API key/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Ollama branch
// ---------------------------------------------------------------------------

describe('Ollama branch', () => {
  it('OllamaUrlField receives the primary instance URL', () => {
    const settings = makeSettings(
      { provider: 'ollama' },
      {},
      { ollama_instances: [makeOllamaInstance({ base_url: 'http://myhost:11434' })] },
    );
    render(<EmbeddingProviderConfig settings={settings} setSettings={vi.fn()} />);
    expect(ollamaUrlFieldProps.url).toBe('http://myhost:11434');
  });

  it('OllamaUrlField url is "" when instances list is empty', () => {
    const settings = makeSettings({ provider: 'ollama' }, {}, { ollama_instances: [] });
    render(<EmbeddingProviderConfig settings={settings} setSettings={vi.fn()} />);
    expect(ollamaUrlFieldProps.url).toBe('');
  });

  it('handleOllamaUrlChange — empty instances → pushes new primary instance', () => {
    const setSettings = vi.fn();
    const settings = makeSettings({ provider: 'ollama' }, {}, { ollama_instances: [] });
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    fireEvent.change(screen.getByTestId('ollama-url-input'), {
      target: { value: 'http://new-host:11434' },
    });

    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.ollama_instances).toHaveLength(1);
    expect(payload.llm.ollama_instances[0].base_url).toBe('http://new-host:11434');
    expect(payload.llm.ollama_instances[0].id).toBe('primary');
    expect(payload.llm.ollama_instances[0].enabled).toBe(true);
  });

  it('handleOllamaUrlChange — existing instances → updates instances[0].base_url and sets enabled=true', () => {
    const setSettings = vi.fn();
    const settings = makeSettings(
      { provider: 'ollama' },
      {},
      { ollama_instances: [makeOllamaInstance({ base_url: 'http://old:11434', enabled: false })] },
    );
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    fireEvent.change(screen.getByTestId('ollama-url-input'), {
      target: { value: 'http://updated:11434' },
    });

    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.ollama_instances[0].base_url).toBe('http://updated:11434');
    expect(payload.llm.ollama_instances[0].enabled).toBe(true);
  });

  it('handleOllamaUrlChange clears verification (sets null)', async () => {
    const setSettings = vi.fn();
    const settings = makeSettings({ provider: 'ollama' });
    render(<EmbeddingProviderConfig settings={settings} setSettings={setSettings} />);

    // First verify to set verification state
    mockVerifyOllamaUrl.mockResolvedValueOnce({ success: true, message: 'OK' });
    fireEvent.click(screen.getByTestId('ollama-verify-btn'));
    // Wait for async resolution so state is settled before the next interaction
    await waitFor(() => {
      expect(ollamaUrlFieldProps.verification).toEqual({ success: true, message: 'OK' });
    });

    // Then change URL — verification should clear
    fireEvent.change(screen.getByTestId('ollama-url-input'), {
      target: { value: 'http://new:11434' },
    });

    // Props passed to OllamaUrlField after url change should have verification=null
    expect(ollamaUrlFieldProps.verification).toBeNull();
  });

  it('handleVerifyOllamaUrl — success path stores result from settingsApi', async () => {
    const successResponse: OllamaVerifyResponse = {
      success: true,
      message: 'Connected',
      version: '0.1.0',
      model_count: 5,
    };
    mockVerifyOllamaUrl.mockResolvedValueOnce(successResponse);

    const settings = makeSettings(
      { provider: 'ollama' },
      {},
      { ollama_instances: [makeOllamaInstance({ base_url: 'http://localhost:11434' })] },
    );
    render(<EmbeddingProviderConfig settings={settings} setSettings={vi.fn()} />);

    fireEvent.click(screen.getByTestId('ollama-verify-btn'));

    await waitFor(() => {
      expect(ollamaUrlFieldProps.verification).toEqual(successResponse);
    });
    expect(mockVerifyOllamaUrl).toHaveBeenCalledWith('http://localhost:11434');
  });

  it('handleVerifyOllamaUrl — catch path → sets {success:false, message:"Verification request failed"}', async () => {
    mockVerifyOllamaUrl.mockRejectedValueOnce(new Error('Network error'));

    const settings = makeSettings(
      { provider: 'ollama' },
      {},
      { ollama_instances: [makeOllamaInstance({ base_url: 'http://localhost:11434' })] },
    );
    render(<EmbeddingProviderConfig settings={settings} setSettings={vi.fn()} />);

    fireEvent.click(screen.getByTestId('ollama-verify-btn'));

    await waitFor(() => {
      expect(ollamaUrlFieldProps.verification).toEqual({
        success: false,
        message: 'Verification request failed',
      });
    });
  });

  it('handleVerifyOllamaUrl — no-op when url is blank (trim check)', async () => {
    const settings = makeSettings({ provider: 'ollama' }, {}, { ollama_instances: [] });
    render(<EmbeddingProviderConfig settings={settings} setSettings={vi.fn()} />);

    fireEvent.click(screen.getByTestId('ollama-verify-btn'));

    // Should NOT call the API
    await waitFor(() => {
      expect(mockVerifyOllamaUrl).not.toHaveBeenCalled();
    });
  });

  it('onClearVerification callback resets verification to null', async () => {
    const successResponse: OllamaVerifyResponse = { success: true, message: 'OK' };
    mockVerifyOllamaUrl.mockResolvedValueOnce(successResponse);

    const settings = makeSettings(
      { provider: 'ollama' },
      {},
      { ollama_instances: [makeOllamaInstance({ base_url: 'http://localhost:11434' })] },
    );
    render(<EmbeddingProviderConfig settings={settings} setSettings={vi.fn()} />);

    fireEvent.click(screen.getByTestId('ollama-verify-btn'));

    await waitFor(() => {
      expect(ollamaUrlFieldProps.verification).toEqual(successResponse);
    });

    // Now clear
    fireEvent.click(screen.getByTestId('ollama-clear-btn'));

    expect(ollamaUrlFieldProps.verification).toBeNull();
  });

  it('forwards verifying=true to OllamaUrlField while request is in flight', async () => {
    // Never resolves during test
    mockVerifyOllamaUrl.mockReturnValueOnce(new Promise<OllamaVerifyResponse>(() => {}));

    const settings = makeSettings(
      { provider: 'ollama' },
      {},
      { ollama_instances: [makeOllamaInstance({ base_url: 'http://localhost:11434' })] },
    );
    render(<EmbeddingProviderConfig settings={settings} setSettings={vi.fn()} />);

    fireEvent.click(screen.getByTestId('ollama-verify-btn'));

    await waitFor(() => {
      expect(ollamaUrlFieldProps.verifying).toBe(true);
    });
  });
});
