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
  Link,
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
import { LEADERBOARD_URL } from '../../../constants/config';
import modelScores from '../../../data/modelScores.json';
import type { OllamaModelOption } from './ModelConfig';

type ModelScore = {
  id: string;
  label: string;
  vram_gb: number | null;
  license: string | null;
  isolated: { passed: number; total: number; pct: number } | null;
  chunks: { passed: number; total: number; pct: number } | null;
  notes: string[];
  // Whether `ollama show` lists `tools` under Capabilities; null when unknown.
  tools: boolean | null;
  // Grounded-chat board; pct and passed are null when the run hit the time limit.
  chat: { passed: number | null; total: number | null; pct: number | null; timed_out: boolean } | null;
  // Set on harness-track rows (a model run through an MCP client such as
  // Claude Code). The exporter already leaves them out of this file; the
  // picker filters too, because they are not Ollama models.
  harness?: string | null;
  // The leaderboard's blended percentages (one decimal); null where a board was not run.
  // Extraction = in chunks x 2/3 + isolated x 1/3; Overall = 0.6 x extraction + 0.4 x chat.
  scores: { extraction: number | null; chat: number | null; overall: number | null };
};

// Chat and extraction models come from the benchmark (src/data/modelScores.json,
// written by scripts/benchmark/export_leaderboard.py): every measured model, best
// first, with `fits` saying whether it fits the selected VRAM preset. Both
// dropdowns show the leaderboard's blended scores, read from `scores` in the
// JSON so the app and the site agree: Extraction (in chunks counted twice,
// isolated once) ranks the extraction list, grounded Chat ranks the chat list,
// and each description adds the Overall score. A chat model the app cannot run (no tool calling, or the benchmark
// run did not finish) is marked `usable: false` so it lands in Not recommended.
// Familiar names without a measurement are listed separately so nobody mistakes
// "in the list" for "tested".
const NOT_YET_MEASURED_CHAT_MODELS = [
  { id: 'gpt-oss:120b', name: 'GPT-OSS 120B', description: 'No benchmark data yet, 96GB+ VRAM' },
];
const NOT_YET_MEASURED_EXTRACTION_MODELS = [
  { id: 'gpt-oss:120b', name: 'GPT-OSS 120B', description: 'No benchmark data yet, 96GB+ VRAM' },
];
// Weights alone are not the whole footprint: the context window needs room too.
// The number comes from the exporter so the leaderboard page uses the same one.
const VRAM_HEADROOM_GB: number = modelScores.vram_headroom_gb;

const rate = (s: { passed: number; total: number } | null) => (s ? s.passed / s.total : -1);

/** A blended score for sorting; null (board not run) sorts last. */
const blended = (v: number | null) => v ?? -1;

const overallSuffix = (m: ModelScore) =>
  typeof m.scores.overall === 'number' ? ` Overall ${Math.round(m.scores.overall)}%.` : '';

const notesSuffix = (m: ModelScore) => (m.notes.length ? `. ${m.notes.join(', ')}` : '');

function extractionScore(m: ModelScore): number {
  return typeof m.scores.extraction === 'number' ? Math.round(m.scores.extraction) : m.chunks!.pct;
}

function extractionDescription(m: ModelScore): string {
  const chunks = m.chunks!;
  const isolated = m.isolated ? `, ${m.isolated.passed}/${m.isolated.total} isolated` : '';
  return (
    `Extraction ${extractionScore(m)}%: ${chunks.passed}/${chunks.total} in chunks${isolated}.` +
    `${overallSuffix(m)} ${m.vram_gb ?? '?'} GB weights${notesSuffix(m)}`
  );
}

const fitsVram = (m: ModelScore, vramGb: number | undefined) =>
  vramGb === undefined || m.vram_gb === null || m.vram_gb <= vramGb - VRAM_HEADROOM_GB;

/** Every measured model, best first; `fits` says whether it belongs in Recommended. */
function measuredExtractionModels(vramGb: number | undefined): OllamaModelOption[] {
  return (modelScores.models as ModelScore[])
    .filter((m) => m.chunks && m.harness == null)
    .sort(
      (a, b) =>
        blended(b.scores.extraction) - blended(a.scores.extraction) ||
        rate(b.chunks) - rate(a.chunks) ||
        rate(b.isolated) - rate(a.isolated),
    )
    .map((m) => ({
      id: m.id,
      name: m.label,
      description: extractionDescription(m),
      score: extractionScore(m),
      measured: true,
      fits: fitsVram(m, vramGb),
    }));
}

/** The leaderboard's chat score, rounded; undefined when the run did not finish. */
function chatScore(m: ModelScore): number | undefined {
  if (m.chat!.timed_out) return undefined;
  return typeof m.scores.chat === 'number' ? Math.round(m.scores.chat) : undefined;
}

function chatDescription(m: ModelScore): string {
  const chat = m.chat!;
  const weights = `${m.vram_gb ?? '?'} GB weights`;
  if (m.tools === false) return `No tool calling, so the app's chat cannot use it. ${weights}`;
  if (chat.timed_out) return `Did not finish the chat benchmark (hit the time limit). ${weights}`;
  const score = chatScore(m);
  return `Chat ${score}%: ${chat.passed}/${chat.total} grounded questions answered right.${overallSuffix(m)} ${weights}${notesSuffix(m)}`;
}

/** Every model on the grounded-chat board, best first; `usable: false` marks one the app cannot run. */
function measuredChatModels(vramGb: number | undefined): OllamaModelOption[] {
  return (modelScores.models as ModelScore[])
    .filter((m) => m.chat && m.harness == null)
    // A run that did not finish scores 0 for chat, so it sorts below every finished one.
    .sort((a, b) => blended(b.scores.chat) - blended(a.scores.chat) || rate(b.chunks) - rate(a.chunks))
    .map((m) => ({
      id: m.id,
      name: m.label,
      description: chatDescription(m),
      score: chatScore(m),
      measured: true,
      fits: fitsVram(m, vramGb),
      usable: !(m.chat!.timed_out || m.tools === false),
    }));
}

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

  const chatOptions = useMemo<OllamaModelOption[]>(
    () => [
      ...measuredChatModels(currentPreset?.vram_gb),
      ...NOT_YET_MEASURED_CHAT_MODELS.map((m) => ({ ...m, measured: false })),
    ],
    [currentPreset?.vram_gb],
  );
  const extractionOptions = useMemo<OllamaModelOption[]>(
    () => [
      ...measuredExtractionModels(currentPreset?.vram_gb),
      ...NOT_YET_MEASURED_EXTRACTION_MODELS.map((m) => ({ ...m, measured: false })),
    ],
    [currentPreset?.vram_gb],
  );
  const pretestedIds = useMemo(() => {
    const ids = new Set<string>();
    for (const m of chatOptions) ids.add(m.id);
    for (const m of extractionOptions) ids.add(m.id);
    return ids;
  }, [chatOptions, extractionOptions]);

  // Models installed but in neither the chat nor the extraction list
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
        <Typography variant="body2" gutterBottom sx={{ color: 'text.secondary' }}>
          Not sure which chat or extraction model to run?{' '}
          <Link href={LEADERBOARD_URL} target="_blank" rel="noopener">
            Compare local models on the leaderboard
          </Link>
          , filtered by the VRAM you have.
        </Typography>

        {/* Model Selection - Chat, Extraction, and Vision side by side */}
        <Box sx={{ display: 'flex', flexDirection: { xs: 'column', lg: 'row' }, gap: 2, my: 2 }}>
          <OllamaAutocomplete
            label="Chat Model"
            options={chatOptions}
            scoreTitle="Grounded chat score as on the leaderboard"
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
            options={extractionOptions}
            scoreTitle="Extraction score as on the leaderboard: in chunks counted twice, isolated once"
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
            contextMin={8192}
            contextMax={131072}
            contextStep={2048}
            contextMarks={[
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
