// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for OllamaModelSelector.
 *
 * The component renders a configuration summary card with three
 * OllamaAutocomplete instances, an optional context breakdown bar,
 * an optional context window slider, and two dialogs (model info +
 * remove confirmation). We mock the `useOllamaModels` hook and the
 * heavy MUI-based child components from `./ModelConfig` and
 * `../../../components` so the test focuses on the component's own
 * branching logic (early-return guard, advanced toggle, dialog open
 * state, callback wiring) instead of MUI internals.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// ---------------------------------------------------------------------------
// Mocks — declared BEFORE the SUT import so vitest hoists them correctly.
// ---------------------------------------------------------------------------

const mockUseOllamaModels = vi.fn();
vi.mock('../../../../hooks/useOllamaModels', () => ({
  useOllamaModels: (...args: unknown[]) => mockUseOllamaModels(...args),
}));

// Render OllamaAutocomplete as a minimal harness that exposes the props
// we care about: label (so we can find each instance) and callback buttons
// for onPull / onRemove / onShowInfo / onChange / onInputChange.
vi.mock('../ModelConfig', () => ({
  OllamaAutocomplete: (props: {
    label: string;
    value: string;
    options: { id: string; name: string; description: string }[];
    otherInstalledModels?: { id: string; name: string }[];
    installedModels?: Set<string>;
    onChange: (v: string) => void;
    onInputChange: (v: string) => void;
    onPull?: (id: string) => void;
    onRemove?: (id: string) => void;
    onShowInfo?: (id: string) => void;
  }) => (
    <div data-testid={`autocomplete-${props.label}`}>
      <span data-testid={`autocomplete-value-${props.label}`}>{props.value}</span>
      <span data-testid={`autocomplete-options-count-${props.label}`}>{props.options.length}</span>
      <span data-testid={`autocomplete-other-count-${props.label}`}>{props.otherInstalledModels?.length ?? 0}</span>
      <button onClick={() => props.onChange('picked-model')}>change-{props.label}</button>
      <button onClick={() => props.onChange('')}>change-empty-{props.label}</button>
      <button onClick={() => props.onInputChange('typed-model')}>input-{props.label}</button>
      <button onClick={() => props.onInputChange('')}>input-empty-{props.label}</button>
      <button onClick={() => props.onPull?.('pull-me')}>pull-{props.label}</button>
      <button onClick={() => props.onRemove?.('remove-me')}>remove-{props.label}</button>
      <button onClick={() => props.onShowInfo?.('info-me')}>info-{props.label}</button>
    </div>
  ),
  ContextWindowSlider: (props: {
    contextValue: number;
    onContextChange: (v: number) => void;
  }) => (
    <div data-testid="context-slider">
      <span data-testid="context-slider-value">{props.contextValue}</span>
      <button onClick={() => props.onContextChange(16384)}>slider-set</button>
    </div>
  ),
}));

vi.mock('../../../../components', () => ({
  ContextBreakdownBar: (props: { contextWindow: number; maxOutputTokens: number }) => (
    <div data-testid="context-breakdown">
      <span data-testid="ctx-window">{props.contextWindow}</span>
      <span data-testid="ctx-max-output">{props.maxOutputTokens}</span>
    </div>
  ),
}));

// Import SUT after mocks.
import { OllamaModelSelector } from '../OllamaModelSelector';
import type { Settings, VRAMPreset, OllamaModelShowResponse } from '../../../../types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeSettings(overrides?: Partial<Settings['llm']>): Settings {
  return {
    llm: {
      chat_provider: 'ollama',
      ollama_chat_model: 'qwen3:30b',
      ollama_extraction_model: 'phi4:14b',
      ollama_vision_model: 'qwen3-vl:8b',
      ollama_num_ctx: 8192,
      ai_context_window: undefined,
      ai_max_tokens: undefined,
      ...overrides,
    },
    chunking: {
      group_size: 4,
      small_chunk_size: 1200,
      output_tokens_per_chunk: 2000,
    },
  } as unknown as Settings;
}

const preset: VRAMPreset = {
  name: 'mid',
  display_name: 'Mid Tier',
  description: 'A 24GB preset',
  vram_gb: 24,
  gpu_examples: [],
  version: '1',
  author: 'test',
  builtin: true,
  ollama_settings: {
    ollama_chat_model: 'qwen3:30b',
    ollama_num_ctx: 8192,
  },
  llm_settings: { ai_max_tokens: 4096, enable_thinking: false },
};

function defaultHookValue() {
  return {
    modelsData: null,
    loading: false,
    installedModels: new Set<string>(['qwen3:30b', 'custom-model:7b']),
    pullProgress: {},
    refresh: vi.fn(),
    pullModel: vi.fn(),
    removeModel: vi.fn().mockResolvedValue(true),
    showModel: vi.fn(),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseOllamaModels.mockReturnValue(defaultHookValue());
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('OllamaModelSelector — early return', () => {
  it('returns null when currentPreset is undefined', () => {
    const { container } = render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={undefined}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});

describe('OllamaModelSelector — render shape', () => {
  it('renders all three autocompletes with preset description', () => {
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    expect(screen.getByText('A 24GB preset')).toBeInTheDocument();
    expect(screen.getByTestId('autocomplete-Chat Model')).toBeInTheDocument();
    expect(screen.getByTestId('autocomplete-Extraction Model')).toBeInTheDocument();
    expect(screen.getByTestId('autocomplete-Vision Model (Optional)')).toBeInTheDocument();
  });

  it('passes current model values through to each autocomplete', () => {
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    expect(screen.getByTestId('autocomplete-value-Chat Model').textContent).toBe('qwen3:30b');
    expect(screen.getByTestId('autocomplete-value-Extraction Model').textContent).toBe('phi4:14b');
    expect(screen.getByTestId('autocomplete-value-Vision Model (Optional)').textContent).toBe('qwen3-vl:8b');
  });

  it('coerces null extraction/vision models to empty string for the value prop', () => {
    render(
      <OllamaModelSelector
        settings={makeSettings({ ollama_extraction_model: null, ollama_vision_model: null })}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    expect(screen.getByTestId('autocomplete-value-Extraction Model').textContent).toBe('');
    expect(screen.getByTestId('autocomplete-value-Vision Model (Optional)').textContent).toBe('');
  });

  it('includes a placeholder "None (Disabled)" option for vision in addition to PRETESTED_VISION_MODELS', () => {
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    // PRETESTED_VISION_MODELS has 3 entries + 1 placeholder = 4
    expect(screen.getByTestId('autocomplete-options-count-Vision Model (Optional)').textContent).toBe('4');
  });
});

describe('OllamaModelSelector — otherInstalledModels filtering', () => {
  it('filters out pretested ids and sorts the rest alphabetically', () => {
    mockUseOllamaModels.mockReturnValue({
      ...defaultHookValue(),
      installedModels: new Set([
        'qwen3:30b', // pretested chat
        'phi4:14b',  // pretested extraction
        'zeta:1b',
        'alpha:1b',
      ]),
    });
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    // alpha and zeta should be the only ones left → count = 2
    expect(screen.getByTestId('autocomplete-other-count-Chat Model').textContent).toBe('2');
  });
});

describe('OllamaModelSelector — advanced mode toggling', () => {
  it('does NOT render context breakdown or slider when showAdvanced is false', () => {
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    expect(screen.queryByTestId('context-breakdown')).not.toBeInTheDocument();
    expect(screen.queryByTestId('context-slider')).not.toBeInTheDocument();
  });

  it('renders context breakdown + slider when showAdvanced is true', () => {
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={true}
        currentPreset={preset}
      />,
    );
    expect(screen.getByTestId('context-breakdown')).toBeInTheDocument();
    expect(screen.getByTestId('context-slider')).toBeInTheDocument();
    expect(screen.getByTestId('context-slider-value').textContent).toBe('8192');
  });

  it('falls back through ai_context_window → ollama_num_ctx → 8192 default for breakdown', () => {
    render(
      <OllamaModelSelector
        settings={makeSettings({ ai_context_window: 32768, ollama_num_ctx: 16384 })}
        setSettings={vi.fn()}
        showAdvanced={true}
        currentPreset={preset}
      />,
    );
    // ai_context_window wins
    expect(screen.getByTestId('ctx-window').textContent).toBe('32768');
    // ai_max_tokens is undefined → floor(32768 * 0.25) = 8192
    expect(screen.getByTestId('ctx-max-output').textContent).toBe('8192');
  });

  it('uses explicit ai_max_tokens when provided', () => {
    render(
      <OllamaModelSelector
        settings={makeSettings({ ai_context_window: 32768, ai_max_tokens: 4096 })}
        setSettings={vi.fn()}
        showAdvanced={true}
        currentPreset={preset}
      />,
    );
    expect(screen.getByTestId('ctx-max-output').textContent).toBe('4096');
  });
});

describe('OllamaModelSelector — setSettings callbacks', () => {
  it('invokes setSettings when chat-model onChange fires with a value', () => {
    const setSettings = vi.fn();
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    fireEvent.click(screen.getByText('change-Chat Model'));
    expect(setSettings).toHaveBeenCalledTimes(1);
    const [arg] = setSettings.mock.calls[0];
    expect(arg.llm.ollama_chat_model).toBe('picked-model');
  });

  it('coerces empty string to null for extraction model on change', () => {
    const setSettings = vi.fn();
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    fireEvent.click(screen.getByText('change-empty-Extraction Model'));
    expect(setSettings.mock.calls[0][0].llm.ollama_extraction_model).toBeNull();
  });

  it('coerces empty string to null for vision model on input change', () => {
    const setSettings = vi.fn();
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    fireEvent.click(screen.getByText('input-empty-Vision Model (Optional)'));
    expect(setSettings.mock.calls[0][0].llm.ollama_vision_model).toBeNull();
  });

  it('passes typed input through for extraction model', () => {
    const setSettings = vi.fn();
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    fireEvent.click(screen.getByText('input-Extraction Model'));
    expect(setSettings.mock.calls[0][0].llm.ollama_extraction_model).toBe('typed-model');
  });

  it('updates ollama_num_ctx, ai_context_window, and extraction_max_tokens together when slider changes', () => {
    const setSettings = vi.fn();
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={true}
        currentPreset={preset}
      />,
    );
    fireEvent.click(screen.getByText('slider-set'));
    expect(setSettings).toHaveBeenCalledTimes(1);
    const next = setSettings.mock.calls[0][0].llm;
    expect(next.ollama_num_ctx).toBe(16384);
    expect(next.ai_context_window).toBe(16384);
    // floor(16384 * 0.8) = 13107
    expect(next.extraction_max_tokens).toBe(13107);
  });
});

describe('OllamaModelSelector — pull/remove/info wiring', () => {
  it('forwards onPull to the useOllamaModels.pullModel callback', () => {
    const hook = defaultHookValue();
    mockUseOllamaModels.mockReturnValue(hook);
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    fireEvent.click(screen.getByText('pull-Chat Model'));
    expect(hook.pullModel).toHaveBeenCalledWith('pull-me');
  });

  it('opens the remove confirmation dialog on remove request', () => {
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    fireEvent.click(screen.getByText('remove-Chat Model'));
    expect(screen.getByText('Remove Model')).toBeInTheDocument();
    // The model name appears inside the dialog body
    expect(screen.getByText('remove-me')).toBeInTheDocument();
  });

  it('cancels the remove dialog without invoking removeModel', () => {
    const hook = defaultHookValue();
    mockUseOllamaModels.mockReturnValue(hook);
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    fireEvent.click(screen.getByText('remove-Chat Model'));
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(hook.removeModel).not.toHaveBeenCalled();
  });

  it('confirms the remove dialog and invokes removeModel with the target id', async () => {
    const hook = defaultHookValue();
    mockUseOllamaModels.mockReturnValue(hook);
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    fireEvent.click(screen.getByText('remove-Extraction Model'));
    fireEvent.click(screen.getByRole('button', { name: /^remove$/i }));
    // Microtask flush so the async callback can resolve.
    await Promise.resolve();
    expect(hook.removeModel).toHaveBeenCalledWith('remove-me');
  });
});

describe('OllamaModelSelector — model info dialog', () => {
  const sampleShow: OllamaModelShowResponse = {
    modelfile: null,
    parameters: 'temperature 0.7\nnum_ctx 8192',
    template: '{{ .Prompt }}',
    details: {
      parameter_size: '7B',
      quantization_level: 'Q4_K_M',
      family: 'qwen',
      format: 'gguf',
    },
    model_info: null,
  };

  it('opens the info dialog with model details after showModel resolves', async () => {
    const hook = defaultHookValue();
    hook.showModel = vi.fn().mockResolvedValue(sampleShow);
    mockUseOllamaModels.mockReturnValue(hook);

    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    fireEvent.click(screen.getByText('info-Chat Model'));
    // Wait for the promise chain to settle.
    await screen.findByText(/Model Info:/);
    expect(screen.getByText(/info-me/)).toBeInTheDocument();
    expect(screen.getByText('Parameters:')).toBeInTheDocument();
    expect(screen.getByText('7B')).toBeInTheDocument();
    expect(screen.getByText('Quantization:')).toBeInTheDocument();
    expect(screen.getByText('Q4_K_M')).toBeInTheDocument();
    expect(screen.getByText('Family:')).toBeInTheDocument();
    expect(screen.getByText('qwen')).toBeInTheDocument();
    expect(screen.getByText('Format:')).toBeInTheDocument();
    expect(screen.getByText('gguf')).toBeInTheDocument();
    expect(screen.getByText('Template')).toBeInTheDocument();
    expect(screen.getByText('{{ .Prompt }}')).toBeInTheDocument();
  });

  it('does NOT open the info dialog when showModel rejects', async () => {
    const hook = defaultHookValue();
    hook.showModel = vi.fn().mockRejectedValue(new Error('boom'));
    mockUseOllamaModels.mockReturnValue(hook);

    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    fireEvent.click(screen.getByText('info-Chat Model'));
    // Microtask flush so the rejected promise propagates through the catch.
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.queryByText(/Model Info:/)).not.toBeInTheDocument();
  });
});

describe('OllamaModelSelector — hook enablement', () => {
  it('passes isOllama=true to useOllamaModels when chat_provider is ollama', () => {
    render(
      <OllamaModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    expect(mockUseOllamaModels).toHaveBeenCalledWith(true);
  });

  it('passes isOllama=false when chat_provider is something else', () => {
    render(
      <OllamaModelSelector
        settings={makeSettings({ chat_provider: 'openai' })}
        setSettings={vi.fn()}
        showAdvanced={false}
        currentPreset={preset}
      />,
    );
    expect(mockUseOllamaModels).toHaveBeenCalledWith(false);
  });
});
