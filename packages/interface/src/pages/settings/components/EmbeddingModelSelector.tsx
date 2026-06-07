// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * EmbeddingModelSelector: Provider-aware embedding model selection component.
 *
 * Uses MUI Autocomplete with controlled inputValue for proper state management.
 * Passes model + dimensions in a single callback to avoid stale closure bugs.
 *
 * Delegates to LocalOllamaSelector or CloudSelector based on provider,
 * with shared model registry fetched via useEmbeddingModels hook.
 */

import { useState, useCallback, useMemo, useRef } from 'react';
import {
  Autocomplete,
  Box,
  TextField,
  Typography,
  Chip,
} from '@mui/material';
import { useOllamaModels } from '../../../hooks/useOllamaModels';
import type { OllamaModelShowResponse } from '../../../types/settings';
import {
  useEmbeddingModels,
  useLocalEmbeddingModels,
  useDownloadLocalEmbeddingModel,
  useDeleteLocalEmbeddingModel,
  type CuratedEmbeddingModel,
  type CloudEmbeddingModel,
  type EmbeddingOption,
} from '../hooks/useEmbeddingModels';
import { ModelOptionItem } from './ModelOptionItem';
import { ModelInfoDialog } from './ModelInfoDialog';
import { OllamaContextMenu, type MenuPosition } from './OllamaContextMenu';
import { logger } from '../../../utils/logger';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface EmbeddingModelSelectorProps {
  provider: string;
  model: string;
  /** Called when model changes. Dimensions included when selecting a curated model. */
  onModelChange: (model: string, dimensions?: number) => void;
}

// ---------------------------------------------------------------------------
// Local / Ollama Selector
// ---------------------------------------------------------------------------

function LocalOllamaSelector({
  provider,
  model,
  curated,
  onModelChange,
}: {
  provider: 'local' | 'ollama';
  model: string;
  curated: CuratedEmbeddingModel[];
  onModelChange: (model: string, dimensions?: number) => void;
}) {
  const isOllama = provider === 'ollama';
  const isLocal = provider === 'local';
  const ollamaHook = useOllamaModels(isOllama);

  // Local model registry (downloaded sentence-transformers models). The query
  // is only enabled for the 'local' provider; download/delete mutations
  // invalidate it so the installed set refreshes automatically.
  const { data: localModels } = useLocalEmbeddingModels(isLocal);
  const localDownloaded = useMemo(
    () => new Set((localModels ?? []).map((m) => m.id)),
    [localModels],
  );
  const downloadLocalModel = useDownloadLocalEmbeddingModel();
  const deleteLocalModel = useDeleteLocalEmbeddingModel();
  const downloading = downloadLocalModel.isPending ? downloadLocalModel.variables ?? null : null;

  // Ollama 3-dot menu state (controlled open keeps dropdown visible)
  const [menuState, setMenuState] = useState<MenuPosition | null>(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const skipCloseRef = useRef(false);
  const [modelInfo, setModelInfo] = useState<OllamaModelShowResponse | null>(null);
  const [infoDialogOpen, setInfoDialogOpen] = useState(false);

  const handleLocalDownload = useCallback(async (modelId: string) => {
    try {
      await downloadLocalModel.mutateAsync(modelId);
    } catch (err) {
      logger.error('Failed to download model:', err);
    }
  }, [downloadLocalModel]);

  const handleDelete = useCallback(async (modelId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteLocalModel.mutateAsync(modelId);
    } catch (err) {
      logger.error('Failed to delete model:', err);
    }
  }, [deleteLocalModel]);

  const handleMenuOpen = (event: React.MouseEvent<HTMLElement>, modelId: string) => {
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    skipCloseRef.current = true;
    setMenuState({ modelId, top: rect.bottom, left: rect.right });
  };

  const handleMenuClose = () => {
    setMenuState(null);
    setDropdownOpen(false);
  };

  const handleShowInfo = useCallback(async (modelId: string) => {
    try {
      const info = await ollamaHook.showModel(modelId);
      setModelInfo(info);
      setInfoDialogOpen(true);
    } catch (err) {
      logger.error('Failed to get model info:', err);
    }
  }, [ollamaHook]);

  const handleOllamaRemove = useCallback(async (modelId: string) => {
    await ollamaHook.removeModel(modelId);
  }, [ollamaHook]);

  // Controlled input text — prevents MUI from clearing it on selection.
  // Synced from the model prop during render (react.dev "adjusting state
  // when a prop changes") rather than in an effect.
  const [inputValue, setInputValue] = useState(model);
  const [prevModel, setPrevModel] = useState(model);
  if (model !== prevModel) {
    setPrevModel(model);
    setInputValue(model);
  }

  // Build options — for local, check download status from localDownloaded set
  const isModelInstalled = (modelId: string): boolean => {
    if (isOllama) return ollamaHook.installedModels.has(modelId);
    if (isLocal) return localDownloaded.has(modelId);
    return true;
  };

  // Filter curated models by provider: CPU gets <=1024d, GPU gets all
  const filteredCurated = isLocal
    ? curated.filter((m) => m.dimensions <= 1024)
    : curated;

  const recommendedOptions: EmbeddingOption[] = filteredCurated.map((m) => ({
    id: isOllama ? m.ollama : m.local,
    name: m.name,
    description: `${m.dimensions}d${m.mrl ? ', MRL' : ''}${m.default ? ' (default)' : ''}`,
    group: 'Recommended',
    installed: isModelInstalled(isOllama ? m.ollama : m.local),
  }));

  const curatedIds = new Set(recommendedOptions.map((o) => o.id));

  // For Ollama: show other installed embedding models
  const otherOllamaOptions: EmbeddingOption[] = isOllama
    ? Array.from(ollamaHook.installedModels)
        .filter((name) => !curatedIds.has(name) && name.toLowerCase().includes('embed'))
        .map((name) => ({ id: name, name, description: '', group: 'Other Installed', installed: true }))
    : [];

  // For Local: show downloaded models not in the curated list
  const otherLocalOptions: EmbeddingOption[] = isLocal
    ? Array.from(localDownloaded)
        .filter((id) => !curatedIds.has(id))
        .map((id) => {
          const name = id.includes('/') ? id.split('/').pop()! : id;
          return { id, name, description: '', group: 'Downloaded', installed: true };
        })
    : [];

  const allOptions = [...recommendedOptions, ...otherOllamaOptions, ...otherLocalOptions];

  // Look up dimensions for a model ID
  const getDimensions = (id: string): number | undefined => {
    const match = curated.find((m) => (isOllama ? m.ollama : m.local) === id);
    return match?.dimensions;
  };

  return (
    <>
      <Autocomplete
        freeSolo
        open={dropdownOpen}
        onOpen={() => setDropdownOpen(true)}
        onClose={() => {
          if (skipCloseRef.current) {
            skipCloseRef.current = false;
            return;
          }
          setDropdownOpen(false);
        }}
        options={allOptions}
        groupBy={(option) => typeof option === 'string' ? '' : option.group}
        getOptionLabel={(option) => typeof option === 'string' ? option : option.id}
        isOptionEqualToValue={(option, val) =>
          typeof val === 'string' ? option.id === val : option.id === val.id
        }
        value={model || ''}
        inputValue={inputValue}
        onChange={(_, newValue) => {
          const modelId = typeof newValue === 'string' ? newValue : newValue?.id || '';
          if (!modelId && model) return;
          setInputValue(modelId);
          onModelChange(modelId, getDimensions(modelId));
          setDropdownOpen(false);
        }}
        onInputChange={(_, newInputValue, reason) => {
          setInputValue(newInputValue);
          if (reason === 'input') {
            onModelChange(newInputValue);
          }
        }}
        filterOptions={(options, state) => {
          if (!state.inputValue || state.inputValue === model) return options;
          const lower = state.inputValue.toLowerCase();
          return options.filter((o) =>
            o.id.toLowerCase().includes(lower) || o.name.toLowerCase().includes(lower)
          );
        }}
        renderOption={(props, option) => (
          <ModelOptionItem
            htmlProps={props}
            option={option}
            activeModelId={model}
            provider={provider}
            isDownloading={downloading === option.id}
            pullProgress={isOllama ? ollamaHook.pullProgress[option.id] : undefined}
            onMenuOpen={handleMenuOpen}
            onPullModel={ollamaHook.pullModel}
            onLocalDownload={handleLocalDownload}
            onLocalDelete={handleDelete}
          />
        )}
        renderInput={(params) => {
          const hasValue = model && model.trim().length > 0;
          const isModelMissing = isOllama && hasValue && ollamaHook.installedModels.size > 0 && !ollamaHook.installedModels.has(model);
          return (
            <TextField
              {...params}
              label="Embedding Model"
              variant="outlined"
              error={!!isModelMissing}
              helperText={isModelMissing ? `${model} is not installed` : undefined}
            />
          );
        }}
        sx={{ flex: 1, minWidth: 0 }}
      />
      {/* Ollama 3-dot context menu */}
      {isOllama && (
        <OllamaContextMenu
          menuState={menuState}
          onClose={handleMenuClose}
          onShowInfo={handleShowInfo}
          onRemove={handleOllamaRemove}
        />
      )}
      {/* Model info dialog */}
      <ModelInfoDialog
        open={infoDialogOpen}
        onClose={() => setInfoDialogOpen(false)}
        modelInfo={modelInfo}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Cloud Selector (OpenAI / Gemini)
// ---------------------------------------------------------------------------

function CloudSelector({
  model,
  cloudModels,
  onModelChange,
}: {
  model: string;
  cloudModels: CloudEmbeddingModel[];
  onModelChange: (model: string, dimensions?: number) => void;
}) {
  // Synced from the model prop during render (see LocalSelector above).
  const [inputValue, setInputValue] = useState(model);
  const [prevModel, setPrevModel] = useState(model);
  if (model !== prevModel) {
    setPrevModel(model);
    setInputValue(model);
  }

  return (
    <Autocomplete
      freeSolo
      options={cloudModels}
      getOptionLabel={(option) => typeof option === 'string' ? option : option.model}
      value={model || ''}
      inputValue={inputValue}
      onChange={(_, newValue) => {
        const newModel = typeof newValue === 'string' ? newValue : newValue?.model || '';
        if (!newModel && model) return;
        setInputValue(newModel);
        const match = cloudModels.find((m) => m.model === newModel);
        onModelChange(newModel, match?.dimensions);
      }}
      onInputChange={(_, newInputValue, reason) => {
        setInputValue(newInputValue);
        if (reason === 'input') {
          onModelChange(newInputValue);
        }
      }}
      filterOptions={(options, state) => {
        if (!state.inputValue || state.inputValue === model) return options;
        const lower = state.inputValue.toLowerCase();
        return options.filter((o) =>
          o.name.toLowerCase().includes(lower) || o.model.toLowerCase().includes(lower)
        );
      }}
      renderOption={(props, option) => (
        <Box component="li" {...props} key={option.model} sx={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start !important' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant="body2" sx={{ fontWeight: 'medium' }}>{option.name}</Typography>
            {option.current && <Chip size="small" label="Current" color="primary" sx={{ height: 18, fontSize: '0.65rem' }} />}
          </Box>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {option.model} · {option.dimensions}d{option.mrl ? ' · MRL' : ''}
          </Typography>
        </Box>
      )}
      renderInput={(params) => (
        <TextField {...params} label="Embedding Model" variant="outlined" />
      )}
      sx={{ flex: 1, minWidth: 0 }}
    />
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export default function EmbeddingModelSelector({
  provider,
  model,
  onModelChange,
}: EmbeddingModelSelectorProps) {
  const registry = useEmbeddingModels();

  if (provider === 'local' || provider === 'ollama') {
    return (
      <LocalOllamaSelector
        provider={provider as 'local' | 'ollama'}
        model={model}
        curated={registry?.curated || []}
        onModelChange={onModelChange}
      />
    );
  }

  if (provider === 'openai' || provider === 'gemini') {
    return (
      <CloudSelector
        model={model}
        cloudModels={registry?.cloud[provider] || []}
        onModelChange={onModelChange}
      />
    );
  }

  return (
    <TextField
      label="Embedding Model"
      variant="outlined"
      fullWidth
      value={model}
      onChange={(e) => onModelChange(e.target.value)}
    />
  );
}
