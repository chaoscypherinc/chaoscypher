// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ModelSelector: Top-level model selection orchestrator for all providers.
 *
 * Routes to the correct provider-specific model selector based on the
 * active chat provider setting. Each provider selector renders its own
 * configuration summary with model autocompletes and context window controls.
 */

import type { Settings, VRAMPreset, CloudModelsResponse } from '../../../types';
import { OllamaModelSelector } from './OllamaModelSelector';
import {
  OpenAIModelSelector,
  AnthropicModelSelector,
  GeminiModelSelector,
} from './ProviderList';

interface ModelSelectorProps {
  /** Current application settings. */
  settings: Settings;
  /** Callback to update settings. */
  setSettings: (settings: Settings) => void;
  /** Whether advanced options are shown. */
  showAdvanced: boolean;
  /** The current VRAM preset (for Ollama summary). */
  currentPreset: VRAMPreset | undefined;
  /** Cloud models registry data. */
  cloudModels: CloudModelsResponse | null;
}

/**
 * Model selection and configuration panel for all providers.
 *
 * Renders the configuration summary card with model autocomplete selectors,
 * context breakdown visualization, and context window sliders (in advanced mode).
 * Adapts its contents based on the active chat provider.
 */
export default function ModelSelector({
  settings,
  setSettings,
  showAdvanced,
  currentPreset,
  cloudModels,
}: ModelSelectorProps) {
  const provider = settings.llm.chat_provider;

  if (provider === 'ollama') {
    return <OllamaModelSelector settings={settings} setSettings={setSettings} showAdvanced={showAdvanced} currentPreset={currentPreset} />;
  }
  if (provider === 'openai') {
    return <OpenAIModelSelector settings={settings} setSettings={setSettings} showAdvanced={showAdvanced} cloudModels={cloudModels} />;
  }
  if (provider === 'anthropic') {
    return <AnthropicModelSelector settings={settings} setSettings={setSettings} showAdvanced={showAdvanced} cloudModels={cloudModels} />;
  }
  if (provider === 'gemini') {
    return <GeminiModelSelector settings={settings} setSettings={setSettings} showAdvanced={showAdvanced} cloudModels={cloudModels} />;
  }
  return null;
}
