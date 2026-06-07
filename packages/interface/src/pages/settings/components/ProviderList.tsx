// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ProviderList: Cloud provider model selector components.
 *
 * Contains OpenAI, Anthropic, and Gemini model selectors,
 * each rendering a configuration summary card with model autocompletes,
 * context breakdown visualization, and context window sliders.
 *
 * The Ollama selector lives in its own module (OllamaModelSelector.tsx).
 */

import {
  Box,
  Typography,
} from '@mui/material';
import TuneIcon from '@mui/icons-material/Tune';
import type { Settings, CloudModelsResponse } from '../../../types';
import { ContextBreakdownBar } from '../../../components';
import {
  CloudModelAutocomplete,
  ContextWindowSlider,
} from './ModelConfig';
import { accentPaperSx, ACCENT_COLORS } from '../../../theme/accentStyles';

// ---------------------------------------------------------------------------
// Shared Cloud Provider Props
// ---------------------------------------------------------------------------

interface CloudModelSelectorProps {
  /** Current application settings. */
  settings: Settings;
  /** Callback to update settings. */
  setSettings: (settings: Settings) => void;
  /** Whether advanced options are shown. */
  showAdvanced: boolean;
  /** Cloud models registry data. */
  cloudModels: CloudModelsResponse | null;
}


// ---------------------------------------------------------------------------
// OpenAI Model Selector
// ---------------------------------------------------------------------------

/** OpenAI configuration summary with model selection and context window controls. */
export function OpenAIModelSelector({ settings, setSettings, showAdvanced, cloudModels }: CloudModelSelectorProps) {
  const models = cloudModels?.providers?.openai?.models || [];

  return (
    <Box sx={{ p: 2, ...accentPaperSx('file') }}>
      <Typography variant="subtitle2" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <TuneIcon sx={{ fontSize: 18, color: ACCENT_COLORS.file }} />
        Configuration Summary
      </Typography>

      {/* Context Breakdown Visualization — advanced mode only, matches Ollama */}
      {showAdvanced && (
        <Box sx={{ mb: 2 }}>
          <ContextBreakdownBar
            contextWindow={settings.llm.openai_context_window || 128000}
            maxOutputTokens={settings.llm.openai_max_output_tokens || 16384}
            groupSize={settings.chunking.group_size}
            inputPerChunk={Math.floor(settings.chunking.small_chunk_size / 4)}
            outputPerChunk={settings.chunking.output_tokens_per_chunk}
          />
        </Box>
      )}

      {/* Model Selection — Chat / Extraction / Vision side-by-side, matching Ollama */}
      <Box sx={{ display: 'flex', flexDirection: { xs: 'column', lg: 'row' }, gap: 2, my: 2 }}>
        <CloudModelAutocomplete
          label="Chat Model"
          options={models}
          value={settings.llm.openai_chat_model}
          onChange={(value, option) => {
            if (option) {
              setSettings({
                ...settings,
                llm: {
                  ...settings.llm,
                  openai_chat_model: option.id,
                  openai_context_window: option.context_window || 128000,
                  openai_max_output_tokens: option.max_output_tokens || 16384,
                  ai_context_window: option.context_window || 128000,
                  extraction_max_tokens: option.max_output_tokens || 16384,
                  ...(option.pricing && {
                    token_cost_input_per_million: option.pricing.input_per_million,
                    token_cost_output_per_million: option.pricing.output_per_million,
                  }),
                },
              });
            } else {
              setSettings({ ...settings, llm: { ...settings.llm, openai_chat_model: value || '' } });
            }
          }}
          onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, openai_chat_model: value } })}
        />
        <CloudModelAutocomplete
          label="Extraction Model"
          helperText="Use a smarter model for entity extraction. Defaults to chat model."
          options={models}
          value={settings.llm.openai_extraction_model || ''}
          onChange={(value) => {
            setSettings({ ...settings, llm: { ...settings.llm, openai_extraction_model: value || null } });
          }}
          onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, openai_extraction_model: value || null } })}
        />
        <CloudModelAutocomplete
          label="Vision Model (Optional)"
          helperText="Describe images in PDFs and image files. Leave empty to disable."
          options={models.filter(m => m.supports_vision)}
          value={settings.llm.openai_vision_model || ''}
          onChange={(value) => {
            setSettings({ ...settings, llm: { ...settings.llm, openai_vision_model: value || null } });
          }}
          onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, openai_vision_model: value || null } })}
        />
      </Box>

      {/* Context Window Slider - Only in advanced mode */}
      {showAdvanced && (
        <ContextWindowSlider
          contextValue={settings.llm.openai_context_window || 128000}
          onContextChange={(ctx) => {
            setSettings({
              ...settings,
              llm: { ...settings.llm, openai_context_window: ctx, ai_context_window: ctx },
            });
          }}
          contextMin={8192}
          contextMax={1048576}
          contextStep={8192}
          contextMarks={[
            { value: 8192, label: '8K' },
            { value: 128000, label: '128K' },
            { value: 1048576, label: '1M' },
          ]}
          outputValue={settings.llm.openai_max_output_tokens || 16384}
          onOutputChange={(tokens) => {
            setSettings({
              ...settings,
              llm: { ...settings.llm, openai_max_output_tokens: tokens, extraction_max_tokens: tokens },
            });
          }}
          outputMin={1024}
          outputMax={100000}
          outputStep={1024}
          outputMarks={[
            { value: 1024, label: '1K' },
            { value: 16384, label: '16K' },
            { value: 100000, label: '100K' },
          ]}
        />
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Anthropic Model Selector
// ---------------------------------------------------------------------------

/** Anthropic configuration summary with model selection and context window controls. */
export function AnthropicModelSelector({ settings, setSettings, showAdvanced, cloudModels }: CloudModelSelectorProps) {
  const models = cloudModels?.providers?.anthropic?.models || [];

  return (
    <Box sx={{ p: 2, ...accentPaperSx('file') }}>
      <Typography variant="subtitle2" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <TuneIcon sx={{ fontSize: 18, color: ACCENT_COLORS.file }} />
        Configuration Summary
      </Typography>

      {/* Context Breakdown Visualization — advanced mode only, matches Ollama */}
      {showAdvanced && (
        <Box sx={{ mb: 2 }}>
          <ContextBreakdownBar
            contextWindow={settings.llm.anthropic_context_window || 200000}
            maxOutputTokens={settings.llm.anthropic_max_output_tokens || 64000}
            groupSize={settings.chunking.group_size}
            inputPerChunk={Math.floor(settings.chunking.small_chunk_size / 4)}
            outputPerChunk={settings.chunking.output_tokens_per_chunk}
          />
        </Box>
      )}

      {/* Model Selection — Chat / Extraction / Vision side-by-side, matching Ollama */}
      <Box sx={{ display: 'flex', flexDirection: { xs: 'column', lg: 'row' }, gap: 2, my: 2 }}>
        <CloudModelAutocomplete
          label="Chat Model"
          options={models}
          value={settings.llm.anthropic_chat_model}
          onChange={(value, option) => {
            if (option) {
              setSettings({
                ...settings,
                llm: {
                  ...settings.llm,
                  anthropic_chat_model: option.id,
                  anthropic_context_window: option.context_window || 200000,
                  anthropic_max_output_tokens: option.max_output_tokens || 64000,
                  ai_context_window: option.context_window || 200000,
                  extraction_max_tokens: option.max_output_tokens || 64000,
                  ...(option.pricing && {
                    token_cost_input_per_million: option.pricing.input_per_million,
                    token_cost_output_per_million: option.pricing.output_per_million,
                  }),
                },
              });
            } else {
              setSettings({ ...settings, llm: { ...settings.llm, anthropic_chat_model: value || '' } });
            }
          }}
          onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, anthropic_chat_model: value } })}
        />
        <CloudModelAutocomplete
          label="Extraction Model"
          helperText="Use a smarter model for entity extraction. Defaults to chat model."
          options={models}
          value={settings.llm.anthropic_extraction_model || ''}
          onChange={(value) => {
            setSettings({ ...settings, llm: { ...settings.llm, anthropic_extraction_model: value || null } });
          }}
          onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, anthropic_extraction_model: value || null } })}
        />
        <CloudModelAutocomplete
          label="Vision Model (Optional)"
          helperText="Describe images in PDFs and image files. Leave empty to disable."
          options={models.filter(m => m.supports_vision)}
          value={settings.llm.anthropic_vision_model || ''}
          onChange={(value) => {
            setSettings({ ...settings, llm: { ...settings.llm, anthropic_vision_model: value || null } });
          }}
          onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, anthropic_vision_model: value || null } })}
        />
      </Box>

      {/* Context Window Slider - Only in advanced mode */}
      {showAdvanced && (
        <ContextWindowSlider
          contextValue={settings.llm.anthropic_context_window || 200000}
          onContextChange={(ctx) => {
            setSettings({
              ...settings,
              llm: { ...settings.llm, anthropic_context_window: ctx, ai_context_window: ctx },
            });
          }}
          contextMin={8192}
          contextMax={1000000}
          contextStep={8192}
          contextMarks={[
            { value: 8192, label: '8K' },
            { value: 200000, label: '200K' },
            { value: 1000000, label: '1M' },
          ]}
          outputValue={settings.llm.anthropic_max_output_tokens || 64000}
          onOutputChange={(tokens) => {
            setSettings({
              ...settings,
              llm: { ...settings.llm, anthropic_max_output_tokens: tokens, extraction_max_tokens: tokens },
            });
          }}
          outputMin={1024}
          outputMax={64000}
          outputStep={1024}
          outputMarks={[
            { value: 1024, label: '1K' },
            { value: 32000, label: '32K' },
            { value: 64000, label: '64K' },
          ]}
        />
      )}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Gemini Model Selector
// ---------------------------------------------------------------------------

/** Gemini configuration summary with model selection and context window controls. */
export function GeminiModelSelector({ settings, setSettings, showAdvanced, cloudModels }: CloudModelSelectorProps) {
  const models = cloudModels?.providers?.gemini?.models || [];

  return (
    <Box sx={{ p: 2, ...accentPaperSx('file') }}>
      <Typography variant="subtitle2" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <TuneIcon sx={{ fontSize: 18, color: ACCENT_COLORS.file }} />
        Configuration Summary
      </Typography>

      {/* Context Breakdown Visualization — advanced mode only, matches Ollama */}
      {showAdvanced && (
        <Box sx={{ mb: 2 }}>
          <ContextBreakdownBar
            contextWindow={settings.llm.gemini_context_window || 1048576}
            maxOutputTokens={settings.llm.gemini_max_output_tokens || 65536}
            groupSize={settings.chunking.group_size}
            inputPerChunk={Math.floor(settings.chunking.small_chunk_size / 4)}
            outputPerChunk={settings.chunking.output_tokens_per_chunk}
          />
        </Box>
      )}

      {/* Model Selection — Chat / Extraction / Vision side-by-side, matching Ollama */}
      <Box sx={{ display: 'flex', flexDirection: { xs: 'column', lg: 'row' }, gap: 2, my: 2 }}>
        <CloudModelAutocomplete
          label="Chat Model"
          options={models}
          value={settings.llm.gemini_chat_model}
          onChange={(value, option) => {
            if (option) {
              setSettings({
                ...settings,
                llm: {
                  ...settings.llm,
                  gemini_chat_model: option.id,
                  gemini_context_window: option.context_window || 1048576,
                  gemini_max_output_tokens: option.max_output_tokens || 65536,
                  ai_context_window: option.context_window || 1048576,
                  extraction_max_tokens: option.max_output_tokens || 65536,
                  ...(option.pricing && {
                    token_cost_input_per_million: option.pricing.input_per_million,
                    token_cost_output_per_million: option.pricing.output_per_million,
                  }),
                },
              });
            } else {
              setSettings({ ...settings, llm: { ...settings.llm, gemini_chat_model: value || '' } });
            }
          }}
          onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, gemini_chat_model: value } })}
        />
        <CloudModelAutocomplete
          label="Extraction Model"
          helperText="Use a smarter model for entity extraction. Defaults to chat model."
          options={models}
          value={settings.llm.gemini_extraction_model || ''}
          onChange={(value) => {
            setSettings({ ...settings, llm: { ...settings.llm, gemini_extraction_model: value || null } });
          }}
          onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, gemini_extraction_model: value || null } })}
        />
        <CloudModelAutocomplete
          label="Vision Model (Optional)"
          helperText="Describe images in PDFs and image files. Leave empty to disable."
          options={models.filter(m => m.supports_vision)}
          value={settings.llm.gemini_vision_model || ''}
          onChange={(value) => {
            setSettings({ ...settings, llm: { ...settings.llm, gemini_vision_model: value || null } });
          }}
          onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, gemini_vision_model: value || null } })}
        />
      </Box>

      {/* Context Window Slider - Only in advanced mode */}
      {showAdvanced && (
        <ContextWindowSlider
          contextValue={settings.llm.gemini_context_window || 1048576}
          onContextChange={(ctx) => {
            setSettings({
              ...settings,
              llm: { ...settings.llm, gemini_context_window: ctx, ai_context_window: ctx },
            });
          }}
          contextMin={8192}
          contextMax={2097152}
          contextStep={8192}
          contextMarks={[
            { value: 8192, label: '8K' },
            { value: 1048576, label: '1M' },
            { value: 2097152, label: '2M' },
          ]}
          outputValue={settings.llm.gemini_max_output_tokens || 65536}
          onOutputChange={(tokens) => {
            setSettings({
              ...settings,
              llm: { ...settings.llm, gemini_max_output_tokens: tokens, extraction_max_tokens: tokens },
            });
          }}
          outputMin={1024}
          outputMax={65536}
          outputStep={1024}
          outputMarks={[
            { value: 1024, label: '1K' },
            { value: 32768, label: '32K' },
            { value: 65536, label: '64K' },
          ]}
        />
      )}
    </Box>
  );
}
