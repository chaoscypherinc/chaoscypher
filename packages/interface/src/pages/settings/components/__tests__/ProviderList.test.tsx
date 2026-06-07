// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for ProviderList.tsx (OpenAI / Anthropic / Gemini selectors).
 *
 * Strategy: mock ModelConfig sub-components and ContextBreakdownBar so each
 * test can introspect the props passed to them and trigger their callbacks
 * directly. This lets us assert on the prop-derivation logic
 * (option lookups, default fallbacks, slider plumbing) and on the
 * setSettings payloads produced by onChange handlers without rendering
 * MUI's full Autocomplete/Slider machinery.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

// ---------------------------------------------------------------------------
// Mocks — capture props for inspection / trigger callbacks per test.
// ---------------------------------------------------------------------------

// Records the most recent props each mocked child was rendered with, keyed
// by the `label` prop (CloudModelAutocomplete) or the testid (others).
const autoCompleteProps: Record<string, unknown> = {};
let sliderProps: Record<string, unknown> | null = null;
let breakdownProps: Record<string, unknown> | null = null;

interface CloudOption {
  id: string;
  display_name?: string;
  context_window?: number;
  max_output_tokens?: number;
  supports_vision?: boolean;
  pricing?: { input_per_million: number; output_per_million: number };
}

interface MockAutocompleteProps {
  label: string;
  options: CloudOption[];
  value: string;
  onChange: (value: string | null, option?: CloudOption) => void;
  onInputChange: (value: string) => void;
  helperText?: string;
}

vi.mock('../ModelConfig', () => ({
  CloudModelAutocomplete: (props: MockAutocompleteProps) => {
    autoCompleteProps[props.label] = props;
    return (
      <div
        data-testid={`autocomplete-${props.label.toLowerCase().replace(/[^a-z]/g, '-')}`}
        data-options-count={props.options.length}
      >
        {props.label}:{props.value}
      </div>
    );
  },
  ContextWindowSlider: (props: Record<string, unknown>) => {
    sliderProps = props;
    return <div data-testid="context-window-slider" />;
  },
}));

vi.mock('../../../../components', () => ({
  ContextBreakdownBar: (props: Record<string, unknown>) => {
    breakdownProps = props;
    return <div data-testid="context-breakdown-bar" />;
  },
}));

import {
  OpenAIModelSelector,
  AnthropicModelSelector,
  GeminiModelSelector,
} from '../ProviderList';
import type { Settings, CloudModelsResponse } from '../../../../types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeSettings(overrides: Partial<Settings['llm']> = {}): Settings {
  return {
    llm: {
      openai_chat_model: 'gpt-4o',
      anthropic_chat_model: 'claude-3-5-sonnet',
      gemini_chat_model: 'gemini-1.5-pro',
      ...overrides,
    },
    chunking: {
      group_size: 4,
      small_chunk_size: 1024,
      output_tokens_per_chunk: 2000,
    },
  } as unknown as Settings;
}

function makeCloudModels(): CloudModelsResponse {
  return {
    providers: {
      openai: {
        display_name: 'OpenAI',
        models: [
          {
            id: 'gpt-5',
            display_name: 'GPT-5',
            context_window: 256000,
            max_output_tokens: 32000,
            supports_vision: true,
            pricing: { input_per_million: 10, output_per_million: 30 },
          },
          {
            id: 'gpt-3.5-turbo',
            display_name: 'GPT-3.5',
            context_window: 16000,
            max_output_tokens: 4096,
            supports_vision: false,
          },
        ],
      },
      anthropic: {
        display_name: 'Anthropic',
        models: [
          {
            id: 'claude-opus-5',
            display_name: 'Claude Opus 5',
            context_window: 400000,
            max_output_tokens: 96000,
            supports_vision: true,
            pricing: { input_per_million: 15, output_per_million: 75 },
          },
        ],
      },
      gemini: {
        display_name: 'Gemini',
        models: [
          {
            id: 'gemini-2.5-pro',
            display_name: 'Gemini 2.5 Pro',
            context_window: 2000000,
            max_output_tokens: 128000,
            supports_vision: true,
            pricing: { input_per_million: 2, output_per_million: 8 },
          },
        ],
      },
    },
  };
}

beforeEach(() => {
  for (const k of Object.keys(autoCompleteProps)) delete autoCompleteProps[k];
  sliderProps = null;
  breakdownProps = null;
});

// ---------------------------------------------------------------------------
// OpenAIModelSelector
// ---------------------------------------------------------------------------

describe('OpenAIModelSelector', () => {
  it('renders Configuration Summary header and three autocompletes', () => {
    render(
      <OpenAIModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    expect(screen.getByText('Configuration Summary')).toBeInTheDocument();
    expect(screen.getByTestId('autocomplete-chat-model')).toBeInTheDocument();
    expect(screen.getByTestId('autocomplete-extraction-model')).toBeInTheDocument();
    expect(screen.getByTestId('autocomplete-vision-model--optional-')).toBeInTheDocument();
  });

  it('hides ContextBreakdownBar and slider when showAdvanced=false', () => {
    render(
      <OpenAIModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    expect(screen.queryByTestId('context-breakdown-bar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('context-window-slider')).not.toBeInTheDocument();
  });

  it('shows ContextBreakdownBar and slider when showAdvanced=true and forwards values', () => {
    render(
      <OpenAIModelSelector
        settings={makeSettings({
          openai_context_window: 200000,
          openai_max_output_tokens: 20000,
        })}
        setSettings={vi.fn()}
        showAdvanced={true}
        cloudModels={makeCloudModels()}
      />,
    );
    expect(screen.getByTestId('context-breakdown-bar')).toBeInTheDocument();
    expect(screen.getByTestId('context-window-slider')).toBeInTheDocument();
    expect(breakdownProps?.contextWindow).toBe(200000);
    expect(breakdownProps?.maxOutputTokens).toBe(20000);
    expect(breakdownProps?.groupSize).toBe(4);
    // small_chunk_size 1024 / 4 = 256
    expect(breakdownProps?.inputPerChunk).toBe(256);
    expect(sliderProps?.contextValue).toBe(200000);
    expect(sliderProps?.outputValue).toBe(20000);
  });

  it('falls back to OpenAI defaults (128000 ctx, 16384 out) when settings are null', () => {
    render(
      <OpenAIModelSelector
        settings={makeSettings({ openai_context_window: null, openai_max_output_tokens: null })}
        setSettings={vi.fn()}
        showAdvanced={true}
        cloudModels={makeCloudModels()}
      />,
    );
    expect(breakdownProps?.contextWindow).toBe(128000);
    expect(breakdownProps?.maxOutputTokens).toBe(16384);
    expect(sliderProps?.contextValue).toBe(128000);
    expect(sliderProps?.outputValue).toBe(16384);
  });

  it('filters vision model options to those supporting vision', () => {
    render(
      <OpenAIModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    // openai fixture: 2 models, 1 supports_vision=true
    expect(screen.getByTestId('autocomplete-chat-model').getAttribute('data-options-count')).toBe('2');
    expect(screen.getByTestId('autocomplete-vision-model--optional-').getAttribute('data-options-count')).toBe('1');
  });

  it('renders with empty model list when cloudModels is null', () => {
    render(
      <OpenAIModelSelector
        settings={makeSettings()}
        setSettings={vi.fn()}
        showAdvanced={false}
        cloudModels={null}
      />,
    );
    expect(screen.getByTestId('autocomplete-chat-model').getAttribute('data-options-count')).toBe('0');
  });

  it('chat-model onChange WITH option: applies id + context + output + pricing', () => {
    const setSettings = vi.fn();
    const settings = makeSettings();
    render(
      <OpenAIModelSelector
        settings={settings}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    const chatProps = autoCompleteProps['Chat Model'] as MockAutocompleteProps;
    chatProps.onChange('gpt-5', {
      id: 'gpt-5',
      context_window: 256000,
      max_output_tokens: 32000,
      pricing: { input_per_million: 10, output_per_million: 30 },
    });
    expect(setSettings).toHaveBeenCalledTimes(1);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.openai_chat_model).toBe('gpt-5');
    expect(payload.llm.openai_context_window).toBe(256000);
    expect(payload.llm.openai_max_output_tokens).toBe(32000);
    expect(payload.llm.ai_context_window).toBe(256000);
    expect(payload.llm.extraction_max_tokens).toBe(32000);
    expect(payload.llm.token_cost_input_per_million).toBe(10);
    expect(payload.llm.token_cost_output_per_million).toBe(30);
  });

  it('chat-model onChange WITHOUT option (free-text): only stores model string', () => {
    const setSettings = vi.fn();
    render(
      <OpenAIModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    const chatProps = autoCompleteProps['Chat Model'] as MockAutocompleteProps;
    chatProps.onChange('custom-finetune-id');
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.openai_chat_model).toBe('custom-finetune-id');
    // pricing untouched on free-text path
    expect(payload.llm.token_cost_input_per_million).toBeUndefined();
  });

  it('extraction-model onChange null clears to null (not "")', () => {
    const setSettings = vi.fn();
    render(
      <OpenAIModelSelector
        settings={makeSettings({ openai_extraction_model: 'gpt-5' })}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    const extProps = autoCompleteProps['Extraction Model'] as MockAutocompleteProps;
    extProps.onChange(null);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.openai_extraction_model).toBeNull();
  });

  it('context slider onContextChange writes both openai_context_window and ai_context_window', () => {
    const setSettings = vi.fn();
    render(
      <OpenAIModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={true}
        cloudModels={makeCloudModels()}
      />,
    );
    (sliderProps?.onContextChange as (ctx: number) => void)(64000);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.openai_context_window).toBe(64000);
    expect(payload.llm.ai_context_window).toBe(64000);
  });

  it('output slider onOutputChange writes both openai_max_output_tokens and extraction_max_tokens', () => {
    const setSettings = vi.fn();
    render(
      <OpenAIModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={true}
        cloudModels={makeCloudModels()}
      />,
    );
    (sliderProps?.onOutputChange as (n: number) => void)(8000);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.openai_max_output_tokens).toBe(8000);
    expect(payload.llm.extraction_max_tokens).toBe(8000);
  });
});

// ---------------------------------------------------------------------------
// AnthropicModelSelector
// ---------------------------------------------------------------------------

describe('AnthropicModelSelector', () => {
  it('falls back to Anthropic defaults (200000 ctx, 64000 out)', () => {
    render(
      <AnthropicModelSelector
        settings={makeSettings({ anthropic_context_window: null, anthropic_max_output_tokens: null })}
        setSettings={vi.fn()}
        showAdvanced={true}
        cloudModels={makeCloudModels()}
      />,
    );
    expect(breakdownProps?.contextWindow).toBe(200000);
    expect(breakdownProps?.maxOutputTokens).toBe(64000);
    expect(sliderProps?.contextValue).toBe(200000);
    expect(sliderProps?.outputValue).toBe(64000);
  });

  it('chat-model onChange with option writes anthropic_* + ai_context_window', () => {
    const setSettings = vi.fn();
    render(
      <AnthropicModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    const chatProps = autoCompleteProps['Chat Model'] as MockAutocompleteProps;
    chatProps.onChange('claude-opus-5', {
      id: 'claude-opus-5',
      context_window: 400000,
      max_output_tokens: 96000,
      pricing: { input_per_million: 15, output_per_million: 75 },
    });
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.anthropic_chat_model).toBe('claude-opus-5');
    expect(payload.llm.anthropic_context_window).toBe(400000);
    expect(payload.llm.anthropic_max_output_tokens).toBe(96000);
    expect(payload.llm.ai_context_window).toBe(400000);
    expect(payload.llm.extraction_max_tokens).toBe(96000);
    expect(payload.llm.token_cost_output_per_million).toBe(75);
  });

  it('chat-model onChange WITHOUT option stores plain string into anthropic_chat_model', () => {
    const setSettings = vi.fn();
    render(
      <AnthropicModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    const chatProps = autoCompleteProps['Chat Model'] as MockAutocompleteProps;
    chatProps.onChange('claude-3-haiku');
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.anthropic_chat_model).toBe('claude-3-haiku');
  });

  it('context slider writes both anthropic_context_window and ai_context_window', () => {
    const setSettings = vi.fn();
    render(
      <AnthropicModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={true}
        cloudModels={makeCloudModels()}
      />,
    );
    (sliderProps?.onContextChange as (n: number) => void)(150000);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.anthropic_context_window).toBe(150000);
    expect(payload.llm.ai_context_window).toBe(150000);
  });
});

// ---------------------------------------------------------------------------
// GeminiModelSelector
// ---------------------------------------------------------------------------

describe('GeminiModelSelector', () => {
  it('falls back to Gemini defaults (1048576 ctx, 65536 out)', () => {
    render(
      <GeminiModelSelector
        settings={makeSettings({ gemini_context_window: null, gemini_max_output_tokens: null })}
        setSettings={vi.fn()}
        showAdvanced={true}
        cloudModels={makeCloudModels()}
      />,
    );
    expect(breakdownProps?.contextWindow).toBe(1048576);
    expect(breakdownProps?.maxOutputTokens).toBe(65536);
    expect(sliderProps?.contextValue).toBe(1048576);
    expect(sliderProps?.outputValue).toBe(65536);
  });

  it('chat-model onChange with option writes gemini_* fields + pricing', () => {
    const setSettings = vi.fn();
    render(
      <GeminiModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    const chatProps = autoCompleteProps['Chat Model'] as MockAutocompleteProps;
    chatProps.onChange('gemini-2.5-pro', {
      id: 'gemini-2.5-pro',
      context_window: 2000000,
      max_output_tokens: 128000,
      pricing: { input_per_million: 2, output_per_million: 8 },
    });
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.gemini_chat_model).toBe('gemini-2.5-pro');
    expect(payload.llm.gemini_context_window).toBe(2000000);
    expect(payload.llm.gemini_max_output_tokens).toBe(128000);
    expect(payload.llm.ai_context_window).toBe(2000000);
    expect(payload.llm.extraction_max_tokens).toBe(128000);
    expect(payload.llm.token_cost_input_per_million).toBe(2);
  });

  it('chat-model onChange with option but no context_window/max_output_tokens uses Gemini defaults', () => {
    const setSettings = vi.fn();
    render(
      <GeminiModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    const chatProps = autoCompleteProps['Chat Model'] as MockAutocompleteProps;
    chatProps.onChange('mystery-model', { id: 'mystery-model' });
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.gemini_context_window).toBe(1048576);
    expect(payload.llm.gemini_max_output_tokens).toBe(65536);
    // No pricing on option -> token_cost_* should not appear
    expect(payload.llm.token_cost_input_per_million).toBeUndefined();
  });

  it('vision-model onChange stores null when value is empty', () => {
    const setSettings = vi.fn();
    render(
      <GeminiModelSelector
        settings={makeSettings({ gemini_vision_model: 'gemini-2.5-pro' })}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    const visionProps = autoCompleteProps['Vision Model (Optional)'] as MockAutocompleteProps;
    visionProps.onChange('');
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.gemini_vision_model).toBeNull();
  });

  it('extraction-model onChange null clears Gemini extraction model', () => {
    const setSettings = vi.fn();
    render(
      <GeminiModelSelector
        settings={makeSettings({ gemini_extraction_model: 'gemini-2.5-pro' })}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Extraction Model'] as MockAutocompleteProps).onChange(null);
    expect((setSettings.mock.calls[0][0] as Settings).llm.gemini_extraction_model).toBeNull();
  });

  it('output slider writes both gemini_max_output_tokens and extraction_max_tokens', () => {
    const setSettings = vi.fn();
    render(
      <GeminiModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={true}
        cloudModels={makeCloudModels()}
      />,
    );
    (sliderProps?.onOutputChange as (n: number) => void)(12345);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.gemini_max_output_tokens).toBe(12345);
    expect(payload.llm.extraction_max_tokens).toBe(12345);
  });

  it('context slider writes both gemini_context_window and ai_context_window', () => {
    const setSettings = vi.fn();
    render(
      <GeminiModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={true}
        cloudModels={makeCloudModels()}
      />,
    );
    (sliderProps?.onContextChange as (n: number) => void)(900000);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.gemini_context_window).toBe(900000);
    expect(payload.llm.ai_context_window).toBe(900000);
  });
});

// ---------------------------------------------------------------------------
// onInputChange + extraction/vision branch coverage across all providers
// ---------------------------------------------------------------------------

describe('onInputChange + extraction/vision handlers', () => {
  it('OpenAI chat onInputChange writes typed string into openai_chat_model', () => {
    const setSettings = vi.fn();
    render(
      <OpenAIModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Chat Model'] as MockAutocompleteProps).onInputChange('gpt-typing');
    expect((setSettings.mock.calls[0][0] as Settings).llm.openai_chat_model).toBe('gpt-typing');
  });

  it('OpenAI extraction onInputChange empty becomes null', () => {
    const setSettings = vi.fn();
    render(
      <OpenAIModelSelector
        settings={makeSettings({ openai_extraction_model: 'gpt-5' })}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Extraction Model'] as MockAutocompleteProps).onInputChange('');
    expect((setSettings.mock.calls[0][0] as Settings).llm.openai_extraction_model).toBeNull();
  });

  it('OpenAI vision onInputChange non-empty value stored verbatim', () => {
    const setSettings = vi.fn();
    render(
      <OpenAIModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Vision Model (Optional)'] as MockAutocompleteProps).onInputChange('gpt-vision-x');
    expect((setSettings.mock.calls[0][0] as Settings).llm.openai_vision_model).toBe('gpt-vision-x');
  });

  it('Anthropic extraction onChange null clears anthropic_extraction_model', () => {
    const setSettings = vi.fn();
    render(
      <AnthropicModelSelector
        settings={makeSettings({ anthropic_extraction_model: 'claude-opus-5' })}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Extraction Model'] as MockAutocompleteProps).onChange(null);
    expect((setSettings.mock.calls[0][0] as Settings).llm.anthropic_extraction_model).toBeNull();
  });

  it('Anthropic extraction onInputChange empty becomes null', () => {
    const setSettings = vi.fn();
    render(
      <AnthropicModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Extraction Model'] as MockAutocompleteProps).onInputChange('');
    expect((setSettings.mock.calls[0][0] as Settings).llm.anthropic_extraction_model).toBeNull();
  });

  it('Anthropic vision onChange empty stores null', () => {
    const setSettings = vi.fn();
    render(
      <AnthropicModelSelector
        settings={makeSettings({ anthropic_vision_model: 'claude-opus-5' })}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Vision Model (Optional)'] as MockAutocompleteProps).onChange('');
    expect((setSettings.mock.calls[0][0] as Settings).llm.anthropic_vision_model).toBeNull();
  });

  it('Anthropic vision onInputChange stores typed value', () => {
    const setSettings = vi.fn();
    render(
      <AnthropicModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Vision Model (Optional)'] as MockAutocompleteProps).onInputChange('claude-vision-pro');
    expect((setSettings.mock.calls[0][0] as Settings).llm.anthropic_vision_model).toBe('claude-vision-pro');
  });

  it('Anthropic chat onInputChange stores typed string', () => {
    const setSettings = vi.fn();
    render(
      <AnthropicModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Chat Model'] as MockAutocompleteProps).onInputChange('claude-custom');
    expect((setSettings.mock.calls[0][0] as Settings).llm.anthropic_chat_model).toBe('claude-custom');
  });

  it('Anthropic output slider writes anthropic_max_output_tokens + extraction_max_tokens', () => {
    const setSettings = vi.fn();
    render(
      <AnthropicModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={true}
        cloudModels={makeCloudModels()}
      />,
    );
    (sliderProps?.onOutputChange as (n: number) => void)(48000);
    const payload = setSettings.mock.calls[0][0] as Settings;
    expect(payload.llm.anthropic_max_output_tokens).toBe(48000);
    expect(payload.llm.extraction_max_tokens).toBe(48000);
  });

  it('Gemini chat onChange WITHOUT option stores plain string', () => {
    const setSettings = vi.fn();
    render(
      <GeminiModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Chat Model'] as MockAutocompleteProps).onChange('gemini-typing');
    expect((setSettings.mock.calls[0][0] as Settings).llm.gemini_chat_model).toBe('gemini-typing');
  });

  it('Gemini chat onInputChange stores typed string', () => {
    const setSettings = vi.fn();
    render(
      <GeminiModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Chat Model'] as MockAutocompleteProps).onInputChange('gemini-x');
    expect((setSettings.mock.calls[0][0] as Settings).llm.gemini_chat_model).toBe('gemini-x');
  });

  it('Gemini extraction onInputChange empty becomes null', () => {
    const setSettings = vi.fn();
    render(
      <GeminiModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Extraction Model'] as MockAutocompleteProps).onInputChange('');
    expect((setSettings.mock.calls[0][0] as Settings).llm.gemini_extraction_model).toBeNull();
  });

  it('Gemini vision onInputChange empty becomes null', () => {
    const setSettings = vi.fn();
    render(
      <GeminiModelSelector
        settings={makeSettings()}
        setSettings={setSettings}
        showAdvanced={false}
        cloudModels={makeCloudModels()}
      />,
    );
    (autoCompleteProps['Vision Model (Optional)'] as MockAutocompleteProps).onInputChange('');
    expect((setSettings.mock.calls[0][0] as Settings).llm.gemini_vision_model).toBeNull();
  });
});
