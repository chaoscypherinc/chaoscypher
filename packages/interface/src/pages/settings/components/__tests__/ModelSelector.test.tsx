// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// Mock the child selectors so this test exercises only the router.
vi.mock('../OllamaModelSelector', () => ({
  OllamaModelSelector: () => <div data-testid="ollama-selector">ollama</div>,
}));
vi.mock('../ProviderList', () => ({
  OpenAIModelSelector: () => <div data-testid="openai-selector">openai</div>,
  AnthropicModelSelector: () => <div data-testid="anthropic-selector">anthropic</div>,
  GeminiModelSelector: () => <div data-testid="gemini-selector">gemini</div>,
}));

import ModelSelector from '../ModelSelector';
import type { Settings, VRAMPreset, CloudModelsResponse } from '../../../../types';

function makeSettings(provider: string): Settings {
  // Cast through unknown so we only need to populate the field under test.
  return { llm: { chat_provider: provider } } as unknown as Settings;
}

const setSettings = vi.fn();
const currentPreset = undefined as VRAMPreset | undefined;
const cloudModels = null as CloudModelsResponse | null;

describe('ModelSelector', () => {
  it('renders OllamaModelSelector when provider=ollama', () => {
    render(
      <ModelSelector
        settings={makeSettings('ollama')}
        setSettings={setSettings}
        showAdvanced={false}
        currentPreset={currentPreset}
        cloudModels={cloudModels}
      />,
    );
    expect(screen.getByTestId('ollama-selector')).toBeInTheDocument();
    expect(screen.queryByTestId('openai-selector')).not.toBeInTheDocument();
  });

  it('renders OpenAIModelSelector when provider=openai', () => {
    render(
      <ModelSelector
        settings={makeSettings('openai')}
        setSettings={setSettings}
        showAdvanced={false}
        currentPreset={currentPreset}
        cloudModels={cloudModels}
      />,
    );
    expect(screen.getByTestId('openai-selector')).toBeInTheDocument();
  });

  it('renders AnthropicModelSelector when provider=anthropic', () => {
    render(
      <ModelSelector
        settings={makeSettings('anthropic')}
        setSettings={setSettings}
        showAdvanced={false}
        currentPreset={currentPreset}
        cloudModels={cloudModels}
      />,
    );
    expect(screen.getByTestId('anthropic-selector')).toBeInTheDocument();
  });

  it('renders GeminiModelSelector when provider=gemini', () => {
    render(
      <ModelSelector
        settings={makeSettings('gemini')}
        setSettings={setSettings}
        showAdvanced={false}
        currentPreset={currentPreset}
        cloudModels={cloudModels}
      />,
    );
    expect(screen.getByTestId('gemini-selector')).toBeInTheDocument();
  });

  it('renders nothing when provider is unrecognized', () => {
    const { container } = render(
      <ModelSelector
        settings={makeSettings('cohere-or-something-new')}
        setSettings={setSettings}
        showAdvanced={false}
        currentPreset={currentPreset}
        cloudModels={cloudModels}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
