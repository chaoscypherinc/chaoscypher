// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for ProviderSelector.tsx
 *
 * Strategy: mock the settingsApi.verifyLLM function to control async
 * behavior, and mock OllamaUrlField to capture/trigger its props directly.
 * The internal CloudKeyTester is exercised indirectly by rendering
 * ProviderSelector with a cloud provider and interacting with the
 * "Test connection" button.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { Settings, VRAMPreset, OllamaVerifyResponse, LLMVerifyResponse } from '../../../../types';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// We mock the entire settings service module to control verifyLLM resolution.
const mockVerifyLLM = vi.fn<(provider: string, apiKey: string) => Promise<LLMVerifyResponse>>();

vi.mock('../../../../services/api/settings', () => ({
  settingsApi: {
    verifyLLM: (...args: [string, string]) => mockVerifyLLM(...args),
  },
}));

// Capture the most recent props OllamaUrlField was rendered with so tests
// can inspect forwarded values and trigger callbacks.
interface OllamaUrlFieldProps {
  url: string;
  onChange: (url: string) => void;
  verification: OllamaVerifyResponse | null;
  onVerify: () => Promise<void>;
  verifying: boolean;
  onClearVerification: () => void;
}

let capturedOllamaProps: OllamaUrlFieldProps | null = null;

vi.mock('../../../../components/settings', () => ({
  OllamaUrlField: (props: OllamaUrlFieldProps) => {
    capturedOllamaProps = props;
    return (
      <div data-testid="ollama-url-field">
        <span data-testid="ollama-url">{props.url}</span>
        <span data-testid="ollama-verifying">{String(props.verifying)}</span>
        <span data-testid="ollama-verification">
          {props.verification ? props.verification.message : 'none'}
        </span>
      </div>
    );
  },
}));

import ProviderSelector from '../ProviderSelector';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeSettings(overrides: Partial<Settings['llm']> = {}): Settings {
  return {
    app_name: 'ChaosCypher',
    current_database: 'test.db',
    data_dir: '/data',
    dark_mode: true,
    auto_enable: false,
    setup_completed: true,
    custom_settings: {},
    workflow_history_limit: 100,
    trigger_history_limit: 100,
    llm: {
      chat_provider: 'ollama',
      ollama_instances: [{ id: '1', name: 'local', base_url: 'http://localhost:11434', enabled: true, healthy: true }],
      ollama_chat_model: 'llama3',
      openai_chat_model: 'gpt-4o',
      openai_base_url: 'https://api.openai.com/v1',
      openai_api_key: null,
      anthropic_chat_model: 'claude-3-5-sonnet',
      anthropic_api_key: null,
      gemini_chat_model: 'gemini-1.5-pro',
      gemini_api_key: null,
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
      ...overrides,
    },
    queue: { queue_host: 'localhost', queue_port: 6379, queue_database: 0, queue_ssl: false },
    embedding: { provider: 'ollama', model: 'nomic-embed-text', ollama_instance_id: '1', max_text_length: 8192 },
    search: { max_search_results: 10, enable_vector_search: true, vector_dimensions: 768, fulltext_language: 'english', enable_auto_embedding: true },
    source_processing: { max_file_size_gb: 1, auto_analyze: true, analysis_depth: 'standard', chunk_overlap: 0.1, chunking_strategy: 'sentence', relationship_confidence_threshold: 0.7 },
    chunking: { small_chunk_size: 512, small_chunk_overlap: 50, min_chunk_size: 100, max_chunk_size: 2000, respect_boundaries: true, group_size: 4, group_overlap: 1, output_tokens_per_chunk: 2000 },
    nlp: { nlp_enable_spacy_ner: true, nlp_enable_dependency_parsing: false, nlp_enable_semantic_embeddings: false, nlp_semantic_model: 'en_core_web_sm', nlp_similarity_threshold: 0.8 },
    export: { export_version: '1.0.0', export_license: 'MIT', export_tags: [], export_derived_from: {}, export_dependencies: {} },
    backup: { enabled: false, interval: 'daily', retention_count: 7, backup_dir: '/backups' },
  } as unknown as Settings;
}

const presets: VRAMPreset[] = [
  {
    name: 'vram-8gb',
    display_name: '8 GB VRAM',
    description: 'For 8 GB GPUs',
    vram_gb: 8,
    gpu_examples: ['RTX 3070', 'RX 6700 XT', 'RTX 4060'],
    version: '1.0',
    author: 'cc',
    builtin: true,
    ollama_settings: {
      ollama_chat_model: 'llama3:8b',
      ollama_num_ctx: 8192,
    },
    llm_settings: { ai_max_tokens: 2048, enable_thinking: false },
  },
  {
    name: 'vram-16gb',
    display_name: '16 GB VRAM',
    description: 'For 16 GB GPUs',
    vram_gb: 16,
    gpu_examples: ['RTX 3080', 'RTX 4080'],
    version: '1.0',
    author: 'cc',
    builtin: true,
    ollama_settings: {
      ollama_chat_model: 'llama3:13b',
      ollama_num_ctx: 16384,
    },
    llm_settings: { ai_max_tokens: 4096, enable_thinking: false },
  },
];

function defaultProps(overrides: Partial<Parameters<typeof ProviderSelector>[0]> = {}) {
  return {
    settings: makeSettings(),
    setSettings: vi.fn(),
    presets,
    applyingPreset: false,
    presetMessage: null,
    setPresetMessage: vi.fn(),
    showAdvanced: false,
    urlVerification: null,
    verifyingUrl: false,
    onApplyPreset: vi.fn<(presetId: string) => Promise<void>>(),
    onVerifyOllamaUrl: vi.fn<() => Promise<void>>(),
    onUrlChange: vi.fn(),
    onClearVerification: vi.fn(),
    primaryOllamaUrl: 'http://localhost:11434',
    onChatProviderChange: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  capturedOllamaProps = null;
  mockVerifyLLM.mockReset();
});

// ---------------------------------------------------------------------------
// Chat Provider Select
// ---------------------------------------------------------------------------

describe('Chat Provider Select', () => {
  it('renders Provider Setup header', () => {
    render(<ProviderSelector {...defaultProps()} />);
    expect(screen.getByText('Provider Setup')).toBeInTheDocument();
  });

  it('calls onChatProviderChange when provider changes', () => {
    const onChatProviderChange = vi.fn();
    render(<ProviderSelector {...defaultProps({ onChatProviderChange })} />);

    // The Chat Provider is the first combobox; open it and pick a new option
    const selects = screen.getAllByRole('combobox');
    fireEvent.mouseDown(selects[0]);

    // Click OpenAI option in the opened listbox
    const option = screen.getByRole('option', { name: 'OpenAI' });
    fireEvent.click(option);

    expect(onChatProviderChange).toHaveBeenCalledWith('openai');
  });

  it('calls onChatProviderChange with "anthropic" when Anthropic option selected', () => {
    const onChatProviderChange = vi.fn();
    render(<ProviderSelector {...defaultProps({ onChatProviderChange })} />);

    const selects = screen.getAllByRole('combobox');
    fireEvent.mouseDown(selects[0]);

    fireEvent.click(screen.getByRole('option', { name: 'Anthropic (Claude)' }));
    expect(onChatProviderChange).toHaveBeenCalledWith('anthropic');
  });

  it('calls onChatProviderChange with "gemini" when Gemini option selected', () => {
    const onChatProviderChange = vi.fn();
    render(<ProviderSelector {...defaultProps({ onChatProviderChange })} />);

    const selects = screen.getAllByRole('combobox');
    fireEvent.mouseDown(selects[0]);

    fireEvent.click(screen.getByRole('option', { name: 'Google Gemini' }));
    expect(onChatProviderChange).toHaveBeenCalledWith('gemini');
  });
});

// ---------------------------------------------------------------------------
// Ollama provider
// ---------------------------------------------------------------------------

describe('Ollama provider', () => {
  it('renders OllamaUrlField when provider is ollama', () => {
    render(<ProviderSelector {...defaultProps()} />);
    expect(screen.getByTestId('ollama-url-field')).toBeInTheDocument();
  });

  it('forwards primaryOllamaUrl to OllamaUrlField', () => {
    render(<ProviderSelector {...defaultProps({ primaryOllamaUrl: 'http://my-ollama:11434' })} />);
    expect(screen.getByTestId('ollama-url')).toHaveTextContent('http://my-ollama:11434');
  });

  it('forwards verifyingUrl to OllamaUrlField', () => {
    render(<ProviderSelector {...defaultProps({ verifyingUrl: true })} />);
    expect(screen.getByTestId('ollama-verifying')).toHaveTextContent('true');
  });

  it('forwards urlVerification to OllamaUrlField', () => {
    const urlVerification: OllamaVerifyResponse = { success: true, message: 'Connected OK' };
    render(<ProviderSelector {...defaultProps({ urlVerification })} />);
    expect(screen.getByTestId('ollama-verification')).toHaveTextContent('Connected OK');
  });

  it('OllamaUrlField onChange calls onUrlChange', () => {
    const onUrlChange = vi.fn();
    render(<ProviderSelector {...defaultProps({ onUrlChange })} />);
    capturedOllamaProps!.onChange('http://new-url:11434');
    expect(onUrlChange).toHaveBeenCalledWith('http://new-url:11434');
  });

  it('OllamaUrlField onClearVerification calls onClearVerification prop', () => {
    const onClearVerification = vi.fn();
    render(<ProviderSelector {...defaultProps({ onClearVerification })} />);
    capturedOllamaProps!.onClearVerification();
    expect(onClearVerification).toHaveBeenCalled();
  });

  it('does not render OllamaUrlField for openai provider', () => {
    render(
      <ProviderSelector
        {...defaultProps({ settings: makeSettings({ chat_provider: 'openai' }) })}
      />,
    );
    expect(screen.queryByTestId('ollama-url-field')).not.toBeInTheDocument();
  });

  it('renders VRAM preset select with preset options when opened', () => {
    render(<ProviderSelector {...defaultProps()} />);
    // Open the GPU VRAM select (second combobox)
    const selects = screen.getAllByRole('combobox');
    fireEvent.mouseDown(selects[1]);
    expect(screen.getByRole('option', { name: /8 GB VRAM/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /16 GB VRAM/i })).toBeInTheDocument();
  });

  it('VRAM preset select change calls onApplyPreset with the preset name', () => {
    const onApplyPreset = vi.fn<(presetId: string) => Promise<void>>().mockResolvedValue(undefined);
    render(<ProviderSelector {...defaultProps({ onApplyPreset })} />);

    // The GPU VRAM select is the second combobox
    const selects = screen.getAllByRole('combobox');
    const vramSelect = selects[1]; // second select is VRAM
    fireEvent.mouseDown(vramSelect);

    fireEvent.click(screen.getByRole('option', { name: /8 GB VRAM/i }));
    expect(onApplyPreset).toHaveBeenCalledWith('vram-8gb');
  });

  it('VRAM preset select is disabled when applyingPreset=true', () => {
    render(<ProviderSelector {...defaultProps({ applyingPreset: true })} />);
    // The GPU VRAM select input should have aria-disabled when disabled
    const selects = screen.getAllByRole('combobox');
    expect(selects[1]).toHaveAttribute('aria-disabled', 'true');
  });

  it('shows helperText with advanced wording when showAdvanced=true', () => {
    render(<ProviderSelector {...defaultProps({ showAdvanced: true })} />);
    expect(
      screen.getByText('Select preset to populate defaults (customize below)'),
    ).toBeInTheDocument();
  });

  it('shows helperText with simple wording when showAdvanced=false', () => {
    render(<ProviderSelector {...defaultProps({ showAdvanced: false })} />);
    expect(
      screen.getByText("Select your GPU's VRAM for optimal settings"),
    ).toBeInTheDocument();
  });

  it('renders presetMessage Alert when presetMessage is set', () => {
    render(
      <ProviderSelector
        {...defaultProps({
          presetMessage: { type: 'success', text: 'Preset applied!' },
        })}
      />,
    );
    expect(screen.getByText('Preset applied!')).toBeInTheDocument();
  });

  it('presetMessage Alert onClose calls setPresetMessage(null)', () => {
    const setPresetMessage = vi.fn();
    render(
      <ProviderSelector
        {...defaultProps({
          presetMessage: { type: 'success', text: 'Preset applied!' },
          setPresetMessage,
        })}
      />,
    );
    // MUI Alert renders a close button when onClose is provided
    const closeButton = screen.getByTitle('Close');
    fireEvent.click(closeButton);
    expect(setPresetMessage).toHaveBeenCalledWith(null);
  });

  it('does not render presetMessage Alert when presetMessage is null', () => {
    render(<ProviderSelector {...defaultProps({ presetMessage: null })} />);
    // No alert text
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// OpenAI provider
// ---------------------------------------------------------------------------

describe('OpenAI provider', () => {
  const openaiProps = () =>
    defaultProps({ settings: makeSettings({ chat_provider: 'openai', openai_api_key: null }) });

  it('renders OpenAI API Key field', () => {
    render(<ProviderSelector {...openaiProps()} />);
    expect(screen.getByLabelText('OpenAI API Key')).toBeInTheDocument();
  });

  it('API key field is empty when value is null (not masked)', () => {
    render(<ProviderSelector {...openaiProps()} />);
    const input = screen.getByLabelText('OpenAI API Key') as HTMLInputElement;
    expect(input.value).toBe('');
    expect(input.placeholder).toBe('');
  });

  it('API key field shows empty value + placeholder when key is masked ("configured")', () => {
    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'openai', openai_api_key: 'configured' }),
        })}
      />,
    );
    const input = screen.getByLabelText('OpenAI API Key') as HTMLInputElement;
    expect(input.value).toBe('');
    expect(input.placeholder).toBe('API key configured (enter new value to change)');
  });

  it('API key field shows actual value when not masked', () => {
    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'openai', openai_api_key: 'sk-test-abc' }),
        })}
      />,
    );
    const input = screen.getByLabelText('OpenAI API Key') as HTMLInputElement;
    expect(input.value).toBe('sk-test-abc');
  });

  it('typing in API key field calls setSettings with updated openai_api_key', () => {
    const setSettings = vi.fn();
    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'openai', openai_api_key: null }),
          setSettings,
        })}
      />,
    );
    fireEvent.change(screen.getByLabelText('OpenAI API Key'), { target: { value: 'sk-new-key' } });
    expect(setSettings).toHaveBeenCalledTimes(1);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.openai_api_key).toBe('sk-new-key');
  });

  it('Base URL field onChange calls setSettings with updated openai_base_url', () => {
    const setSettings = vi.fn();
    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'openai' }),
          setSettings,
        })}
      />,
    );
    const baseUrlInput = screen.getByLabelText('Base URL (Optional)');
    fireEvent.change(baseUrlInput, { target: { value: 'https://my-proxy.example.com/v1' } });
    expect(setSettings).toHaveBeenCalledTimes(1);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.openai_base_url).toBe('https://my-proxy.example.com/v1');
  });

  it('renders "Test connection" button (CloudKeyTester present)', () => {
    render(<ProviderSelector {...openaiProps()} />);
    expect(screen.getByRole('button', { name: /test connection/i })).toBeInTheDocument();
  });

  it('does not render OpenAI fields for ollama provider', () => {
    render(<ProviderSelector {...defaultProps()} />);
    expect(screen.queryByLabelText('OpenAI API Key')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Anthropic provider
// ---------------------------------------------------------------------------

describe('Anthropic provider', () => {
  const anthropicProps = (keyOverride?: string | null) =>
    defaultProps({
      settings: makeSettings({ chat_provider: 'anthropic', anthropic_api_key: keyOverride ?? null }),
    });

  it('renders Anthropic API Key field', () => {
    render(<ProviderSelector {...anthropicProps()} />);
    expect(screen.getByLabelText('Anthropic API Key')).toBeInTheDocument();
  });

  it('API key shows placeholder when masked', () => {
    render(<ProviderSelector {...anthropicProps('configured')} />);
    const input = screen.getByLabelText('Anthropic API Key') as HTMLInputElement;
    expect(input.value).toBe('');
    expect(input.placeholder).toBe('API key configured (enter new value to change)');
  });

  it('API key shows actual value when not masked', () => {
    render(<ProviderSelector {...anthropicProps('sk-ant-mykey')} />);
    const input = screen.getByLabelText('Anthropic API Key') as HTMLInputElement;
    expect(input.value).toBe('sk-ant-mykey');
  });

  it('typing in API key calls setSettings with anthropic_api_key', () => {
    const setSettings = vi.fn();
    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'anthropic', anthropic_api_key: null }),
          setSettings,
        })}
      />,
    );
    fireEvent.change(screen.getByLabelText('Anthropic API Key'), {
      target: { value: 'sk-ant-new' },
    });
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.anthropic_api_key).toBe('sk-ant-new');
  });

  it('renders "Test connection" button (CloudKeyTester present)', () => {
    render(<ProviderSelector {...anthropicProps()} />);
    expect(screen.getByRole('button', { name: /test connection/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Gemini provider
// ---------------------------------------------------------------------------

describe('Gemini provider', () => {
  const geminiProps = (keyOverride?: string | null) =>
    defaultProps({
      settings: makeSettings({ chat_provider: 'gemini', gemini_api_key: keyOverride ?? null }),
    });

  it('renders Gemini API Key field', () => {
    render(<ProviderSelector {...geminiProps()} />);
    expect(screen.getByLabelText('Gemini API Key')).toBeInTheDocument();
  });

  it('API key shows placeholder when masked', () => {
    render(<ProviderSelector {...geminiProps('configured')} />);
    const input = screen.getByLabelText('Gemini API Key') as HTMLInputElement;
    expect(input.value).toBe('');
    expect(input.placeholder).toBe('API key configured (enter new value to change)');
  });

  it('API key shows actual value when not masked', () => {
    render(<ProviderSelector {...geminiProps('AIza-abc123')} />);
    const input = screen.getByLabelText('Gemini API Key') as HTMLInputElement;
    expect(input.value).toBe('AIza-abc123');
  });

  it('typing in API key calls setSettings with gemini_api_key', () => {
    const setSettings = vi.fn();
    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'gemini', gemini_api_key: null }),
          setSettings,
        })}
      />,
    );
    fireEvent.change(screen.getByLabelText('Gemini API Key'), {
      target: { value: 'AIza-new' },
    });
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.gemini_api_key).toBe('AIza-new');
  });

  it('renders "Test connection" button (CloudKeyTester present)', () => {
    render(<ProviderSelector {...geminiProps()} />);
    expect(screen.getByRole('button', { name: /test connection/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// CloudKeyTester — disabled states
// ---------------------------------------------------------------------------

describe('CloudKeyTester disabled states', () => {
  it('Test button is disabled and shows "Enter a key to test" when api key is empty', () => {
    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'openai', openai_api_key: null }),
        })}
      />,
    );
    const button = screen.getByRole('button', { name: /test connection/i });
    expect(button).toBeDisabled();
    expect(screen.getByText('Enter a key to test')).toBeInTheDocument();
  });

  it('Test button is disabled and shows "Re-enter the key to test" when key is masked', () => {
    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'openai', openai_api_key: 'configured' }),
        })}
      />,
    );
    const button = screen.getByRole('button', { name: /test connection/i });
    expect(button).toBeDisabled();
    expect(screen.getByText('Re-enter the key to test')).toBeInTheDocument();
  });

  it('Test button is disabled for anthropic with empty key', () => {
    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'anthropic', anthropic_api_key: null }),
        })}
      />,
    );
    const button = screen.getByRole('button', { name: /test connection/i });
    expect(button).toBeDisabled();
    expect(screen.getByText('Enter a key to test')).toBeInTheDocument();
  });

  it('Test button is disabled for gemini with masked key', () => {
    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'gemini', gemini_api_key: 'configured' }),
        })}
      />,
    );
    const button = screen.getByRole('button', { name: /test connection/i });
    expect(button).toBeDisabled();
    expect(screen.getByText('Re-enter the key to test')).toBeInTheDocument();
  });

  it('Test button is enabled when api key has a real value', () => {
    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'openai', openai_api_key: 'sk-real-key' }),
        })}
      />,
    );
    const button = screen.getByRole('button', { name: /test connection/i });
    expect(button).not.toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// CloudKeyTester — async test flow
// ---------------------------------------------------------------------------

describe('CloudKeyTester async flows', () => {
  it('clicking Test calls verifyLLM with provider and key, shows Testing... then success', async () => {
    const verifyResult: LLMVerifyResponse = {
      success: true,
      message: 'API key valid',
      provider: 'openai',
    };
    mockVerifyLLM.mockResolvedValue(verifyResult);

    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'openai', openai_api_key: 'sk-real-key' }),
        })}
      />,
    );

    const button = screen.getByRole('button', { name: /test connection/i });
    fireEvent.click(button);

    // Button text changes to Testing...
    expect(screen.getByText('Testing…')).toBeInTheDocument();

    // Wait for resolution
    await waitFor(() => {
      expect(screen.getByText('API key valid')).toBeInTheDocument();
    });

    expect(mockVerifyLLM).toHaveBeenCalledWith('openai', 'sk-real-key');
  });

  it('shows error message when verifyLLM returns success=false', async () => {
    const verifyResult: LLMVerifyResponse = {
      success: false,
      message: 'Invalid API key',
      provider: 'anthropic',
    };
    mockVerifyLLM.mockResolvedValue(verifyResult);

    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'anthropic', anthropic_api_key: 'sk-ant-bad' }),
        })}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /test connection/i }));

    await waitFor(() => {
      expect(screen.getByText('Invalid API key')).toBeInTheDocument();
    });

    expect(mockVerifyLLM).toHaveBeenCalledWith('anthropic', 'sk-ant-bad');
  });

  it('shows "Test request failed" when verifyLLM throws', async () => {
    mockVerifyLLM.mockRejectedValue(new Error('Network error'));

    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'gemini', gemini_api_key: 'AIza-realkey' }),
        })}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /test connection/i }));

    await waitFor(() => {
      expect(screen.getByText('Test request failed')).toBeInTheDocument();
    });

    expect(mockVerifyLLM).toHaveBeenCalledWith('gemini', 'AIza-realkey');
  });

  it('Test button is disabled while testing', async () => {
    // Never resolve so we can inspect the in-progress state
    mockVerifyLLM.mockReturnValue(new Promise<LLMVerifyResponse>(() => {}));

    render(
      <ProviderSelector
        {...defaultProps({
          settings: makeSettings({ chat_provider: 'openai', openai_api_key: 'sk-real-key' }),
        })}
      />,
    );

    const button = screen.getByRole('button', { name: /test connection/i });
    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByText('Testing…')).toBeInTheDocument();
    });

    // The button should now be disabled (in testing state)
    expect(button).toBeDisabled();
  });
});
