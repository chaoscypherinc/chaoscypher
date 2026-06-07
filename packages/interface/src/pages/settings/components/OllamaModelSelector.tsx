// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Ollama model selector component.
 *
 * Renders a configuration summary card with chat, extraction, and vision
 * model autocompletes, context breakdown visualization, context window
 * slider, and model info/remove dialogs.
 */

import { useState, useCallback, useMemo } from 'react';
import {
  Box,
  Typography,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Button,
} from '@mui/material';
import TuneIcon from '@mui/icons-material/Tune';
import type { Settings, VRAMPreset, OllamaModelShowResponse } from '../../../types';
import { ContextBreakdownBar } from '../../../components';
import { useOllamaModels } from '../../../hooks/useOllamaModels';
import {
  OllamaAutocomplete,
  ContextWindowSlider,
} from './ModelConfig';
import { accentPaperSx, ACCENT_COLORS } from '../../../theme/accentStyles';
import { logger } from '../../../utils/logger';

// Pre-tested chat models (non-instruct, optimized for reasoning/thinking)
const PRETESTED_CHAT_MODELS = [
  { id: 'qwen3.6:35b-a3b', name: 'Qwen3.6 35B-A3B', description: 'Top MoE, 3B active, 24GB+ VRAM' },
  { id: 'qwen3.6:27b', name: 'Qwen3.6 27B', description: 'Strong dense reasoning, 16-24GB VRAM' },
  { id: 'qwen3.5:30b', name: 'Qwen3.5 30B', description: 'MoE 3B active, 24GB+' },
  { id: 'qwen3.5:27b', name: 'Qwen3.5 27B', description: 'Multimodal, 16-20GB' },
  { id: 'qwen3:30b', name: 'Qwen3 30B', description: 'Proven performer, 24GB+ VRAM' },
  { id: 'qwen3:14b', name: 'Qwen3 14B', description: 'Low tier, 16-20GB VRAM' },
  { id: 'gpt-oss:120b', name: 'GPT-OSS 120B', description: 'Best quality, 96GB+ VRAM' },
];

// Pre-tested extraction models (instruct models for structured output)
const PRETESTED_EXTRACTION_MODELS = [
  { id: 'qwen3.6:35b-a3b', name: 'Qwen3.6 35B-A3B', description: 'Top MoE for extraction, 24GB+ VRAM' },
  { id: 'qwen3.6:27b', name: 'Qwen3.6 27B', description: 'Strong dense, 16-24GB VRAM' },
  { id: 'qwen3:30b-instruct', name: 'Qwen3 30B Instruct', description: 'High tier, 24GB+ VRAM' },
  { id: 'phi4:14b', name: 'Phi-4 14B', description: 'Low tier, 16-20GB VRAM' },
  { id: 'gpt-oss:120b', name: 'GPT-OSS 120B', description: 'Best quality, 96GB+ VRAM' },
  { id: 'qwen2.5:14b-instruct', name: 'Qwen2.5 14B Instruct', description: 'Good instruction following, 16GB' },
];

// Pre-tested vision models (multimodal models that can describe images)
const PRETESTED_VISION_MODELS = [
  { id: 'qwen3-vl:30b', name: 'Qwen3-VL 30B', description: 'Best vision, OCR, charts, 20GB+' },
  { id: 'qwen3-vl:8b', name: 'Qwen3-VL 8B', description: 'Strong vision, lightweight, 6GB+' },
  { id: 'gemma3:27b', name: 'Gemma 3 27B', description: 'Google multimodal, 14GB QAT' },
];


interface OllamaModelSelectorProps {
  /** Current application settings. */
  settings: Settings;
  /** Callback to update settings. */
  setSettings: (settings: Settings) => void;
  /** Whether advanced options are shown. */
  showAdvanced: boolean;
  /** The current VRAM preset (for Ollama summary). */
  currentPreset: VRAMPreset | undefined;
}

/** Ollama configuration summary with model autocompletes and context window slider. */
export function OllamaModelSelector({ settings, setSettings, showAdvanced, currentPreset }: OllamaModelSelectorProps) {
  const isOllama = settings.llm.chat_provider === 'ollama';
  const { installedModels, pullProgress, pullModel, removeModel, showModel } = useOllamaModels(isOllama);

  // Model info dialog state
  const [modelInfo, setModelInfo] = useState<{ name: string; data: OllamaModelShowResponse } | null>(null);
  // Remove confirmation dialog state
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);

  // Build set of all pretested model IDs for filtering
  const pretestedIds = useMemo(() => {
    const ids = new Set<string>();
    for (const m of PRETESTED_CHAT_MODELS) ids.add(m.id);
    for (const m of PRETESTED_EXTRACTION_MODELS) ids.add(m.id);
    return ids;
  }, []);

  // Models installed but not in any pretested list
  const otherInstalledModels = useMemo(() => {
    const result: { id: string; name: string }[] = [];
    for (const name of installedModels) {
      if (!pretestedIds.has(name)) {
        result.push({ id: name, name });
      }
    }
    return result.sort((a, b) => a.name.localeCompare(b.name));
  }, [installedModels, pretestedIds]);

  const handleShowInfo = useCallback(async (modelId: string) => {
    try {
      const data = await showModel(modelId);
      setModelInfo({ name: modelId, data });
    } catch (error) {
      logger.error('Failed to get model info:', error);
    }
  }, [showModel]);

  const handleRemoveRequest = useCallback((modelId: string) => {
    setRemoveTarget(modelId);
  }, []);

  const handleRemoveConfirm = useCallback(async () => {
    if (!removeTarget) return;
    await removeModel(removeTarget);
    setRemoveTarget(null);
  }, [removeTarget, removeModel]);

  if (!currentPreset) return null;

  return (
    <>
      <Box sx={{ p: 2, ...accentPaperSx('file') }}>
        <Typography variant="subtitle2" gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <TuneIcon sx={{ fontSize: 18, color: ACCENT_COLORS.file }} />
          Configuration Summary
        </Typography>
        <Typography variant="body2" gutterBottom sx={{
          color: "text.secondary"
        }}>
          {currentPreset.description}
        </Typography>

        {/* Model Selection - Chat, Extraction, and Vision side by side */}
        <Box sx={{ display: 'flex', flexDirection: { xs: 'column', lg: 'row' }, gap: 2, my: 2 }}>
          <OllamaAutocomplete
            label="Chat Model"
            options={PRETESTED_CHAT_MODELS}
            value={settings.llm.ollama_chat_model}
            onChange={(modelId) => setSettings({ ...settings, llm: { ...settings.llm, ollama_chat_model: modelId } })}
            onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, ollama_chat_model: value } })}
            installedModels={installedModels}
            pullProgress={pullProgress}
            otherInstalledModels={otherInstalledModels}
            onPull={pullModel}
            onRemove={handleRemoveRequest}
            onShowInfo={handleShowInfo}
          />
          <OllamaAutocomplete
            label="Extraction Model"
            options={PRETESTED_EXTRACTION_MODELS}
            value={settings.llm.ollama_extraction_model || ''}
            onChange={(modelId) => setSettings({ ...settings, llm: { ...settings.llm, ollama_extraction_model: modelId || null } })}
            onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, ollama_extraction_model: value || null } })}
            installedModels={installedModels}
            pullProgress={pullProgress}
            otherInstalledModels={otherInstalledModels}
            onPull={pullModel}
            onRemove={handleRemoveRequest}
            onShowInfo={handleShowInfo}
          />
          <OllamaAutocomplete
            label="Vision Model (Optional)"
            options={[
              { id: '', name: 'None (Disabled)', description: 'Disable vision processing' },
              ...PRETESTED_VISION_MODELS,
            ]}
            value={settings.llm.ollama_vision_model || ''}
            onChange={(modelId) => setSettings({ ...settings, llm: { ...settings.llm, ollama_vision_model: modelId || null } })}
            onInputChange={(value) => setSettings({ ...settings, llm: { ...settings.llm, ollama_vision_model: value || null } })}
            installedModels={installedModels}
            pullProgress={pullProgress}
            otherInstalledModels={otherInstalledModels}
            onPull={pullModel}
            onRemove={handleRemoveRequest}
            onShowInfo={handleShowInfo}
          />
        </Box>

        {/* Context Breakdown Visualization - Only in advanced mode */}
        {showAdvanced && (
          <Box sx={{ mb: 2 }}>
            <ContextBreakdownBar
              contextWindow={settings.llm.ai_context_window || settings.llm.ollama_num_ctx || 8192}
              maxOutputTokens={settings.llm.ai_max_tokens || Math.floor((settings.llm.ai_context_window || settings.llm.ollama_num_ctx || 8192) * 0.25)}
              groupSize={settings.chunking.group_size}
              inputPerChunk={Math.floor(settings.chunking.small_chunk_size / 4)}
              outputPerChunk={settings.chunking.output_tokens_per_chunk}
            />
          </Box>
        )}

        {/* Context Window Slider - Only in advanced mode */}
        {showAdvanced && (
          <ContextWindowSlider
            contextValue={settings.llm.ollama_num_ctx || 8192}
            onContextChange={(ctx) => {
              setSettings({
                ...settings,
                llm: {
                  ...settings.llm,
                  ollama_num_ctx: ctx,
                  ai_context_window: ctx,
                  extraction_max_tokens: Math.floor(ctx * 0.8),
                },
              });
            }}
            contextMin={2048}
            contextMax={131072}
            contextStep={2048}
            contextMarks={[
              { value: 2048, label: '2K' },
              { value: 8192, label: '8K' },
              { value: 32768, label: '32K' },
              { value: 65536, label: '64K' },
              { value: 131072, label: '128K' },
            ]}
          />
        )}
      </Box>
      {/* Model Info Dialog */}
      <Dialog open={Boolean(modelInfo)} onClose={() => setModelInfo(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Model Info: {modelInfo?.name}</DialogTitle>
        <DialogContent>
          {modelInfo?.data.details && (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, mb: 2 }}>
              {modelInfo.data.details.parameter_size && (
                <Typography variant="body2"><strong>Parameters:</strong> {modelInfo.data.details.parameter_size}</Typography>
              )}
              {modelInfo.data.details.quantization_level && (
                <Typography variant="body2"><strong>Quantization:</strong> {modelInfo.data.details.quantization_level}</Typography>
              )}
              {modelInfo.data.details.family && (
                <Typography variant="body2"><strong>Family:</strong> {modelInfo.data.details.family}</Typography>
              )}
              {modelInfo.data.details.format && (
                <Typography variant="body2"><strong>Format:</strong> {modelInfo.data.details.format}</Typography>
              )}
            </Box>
          )}
          {modelInfo?.data.parameters && (
            <Box>
              <Typography variant="subtitle2" gutterBottom>Parameters</Typography>
              <Box
                component="pre"
                sx={{
                  bgcolor: 'action.hover',
                  p: 1.5,
                  borderRadius: 1,
                  fontSize: '0.75rem',
                  overflow: 'auto',
                  maxHeight: 200,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {modelInfo.data.parameters}
              </Box>
            </Box>
          )}
          {modelInfo?.data.template && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="subtitle2" gutterBottom>Template</Typography>
              <Box
                component="pre"
                sx={{
                  bgcolor: 'action.hover',
                  p: 1.5,
                  borderRadius: 1,
                  fontSize: '0.75rem',
                  overflow: 'auto',
                  maxHeight: 200,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {modelInfo.data.template}
              </Box>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setModelInfo(null)}>Close</Button>
        </DialogActions>
      </Dialog>
      {/* Remove Confirmation Dialog */}
      <Dialog open={Boolean(removeTarget)} onClose={() => setRemoveTarget(null)}>
        <DialogTitle>Remove Model</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to remove <strong>{removeTarget}</strong> from Ollama? You can re-download it later.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRemoveTarget(null)}>Cancel</Button>
          <Button onClick={handleRemoveConfirm} color="error" variant="outlined">Remove</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
