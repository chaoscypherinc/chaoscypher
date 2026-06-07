// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * EmbeddingProviderConfig — provider + model + dimensions + cloud-key form.
 *
 * Extracted from {@link SearchTab} so the first-run wizard can render the
 * exact same JSX. Stays a controlled component — caller owns the
 * {@link Settings} state and the change handler. Index-rebuild and
 * "needs rebuild" warnings live in SearchTab, which composes this
 * component as its top section.
 *
 * When `embedding.provider === 'ollama'`, an Ollama URL field is also
 * surfaced so a user with cloud-chat + Ollama-embedding can configure
 * the embedding endpoint without flipping back to the LLM tab. The URL
 * is backed by `llm.ollama_instances[0].base_url` — the same field the
 * LLM tab edits — so changes here flow to chat too if it ever switches
 * back to Ollama.
 */

import { useState } from 'react';
import { Box, MenuItem, Paper, TextField, Typography } from '@mui/material';
import MemoryIcon from '@mui/icons-material/Memory';
import EmbeddingModelSelector from './EmbeddingModelSelector';
import { numberInputSx } from './modelConfigStyles';
import { OllamaUrlField } from '../../../components/settings';
import { accentPaperSx, ACCENT_COLORS } from '../../../theme/accentStyles';
import { settingsApi } from '../../../services/api/settings';
import { useEmbeddingModels } from '../hooks/useEmbeddingModels';
import type { OllamaInstance, OllamaVerifyResponse, Settings } from '../../../types';

const EMBEDDING_PROVIDERS = [
  { value: 'local', label: 'Local (CPU)', description: 'HuggingFace sentence-transformers' },
  { value: 'ollama', label: 'Ollama (GPU)', description: 'GPU-accelerated via Ollama' },
  { value: 'openai', label: 'OpenAI', description: 'Cloud API' },
  { value: 'gemini', label: 'Gemini', description: 'Cloud API' },
];

interface EmbeddingProviderConfigProps {
  settings: Settings;
  setSettings: (settings: Settings) => void;
}

export default function EmbeddingProviderConfig({
  settings,
  setSettings,
}: EmbeddingProviderConfigProps) {
  const provider = settings.embedding?.provider || 'local';
  const isCloud = provider === 'openai' || provider === 'gemini';
  const isOllama = provider === 'ollama';

  const [verifyingUrl, setVerifyingUrl] = useState(false);
  const [verification, setVerification] = useState<OllamaVerifyResponse | null>(null);

  // Embedding model registry — used to look up the "best default" model
  // when the user switches providers, so the model field doesn't get
  // stranded on a name that belongs to the previous provider.
  const registry = useEmbeddingModels();

  const updateEmbedding = (field: string, value: string | number) => {
    setSettings({
      ...settings,
      embedding: { ...settings.embedding, [field]: value },
    });
  };

  /**
   * Switch provider and stamp in the registry's recommended model +
   * native dimensions for that provider. Falls back to clearing the
   * model if the registry hasn't loaded yet — the EmbeddingModelSelector
   * will populate as soon as the user opens the dropdown.
   */
  const handleProviderChange = (newProvider: string) => {
    let newModel = '';
    let newDimensions: number | undefined;

    if (newProvider === 'local' || newProvider === 'ollama') {
      const curated = registry?.curated ?? [];
      const def = curated.find((m) => m.default) ?? curated[0];
      if (def) {
        newModel = newProvider === 'local' ? def.local : def.ollama;
        newDimensions = def.dimensions;
      }
    } else {
      const list = registry?.cloud[newProvider] ?? [];
      const first = list.find((m) => m.current) ?? list[0];
      if (first) {
        newModel = first.model;
        newDimensions = first.dimensions;
      }
    }

    const next: Settings = {
      ...settings,
      embedding: { ...settings.embedding, provider: newProvider, model: newModel },
    };
    if (newDimensions !== undefined) {
      next.search = { ...settings.search, vector_dimensions: newDimensions };
    }
    setSettings(next);
  };

  const primaryOllamaUrl = settings.llm.ollama_instances[0]?.base_url ?? '';

  const handleOllamaUrlChange = (newUrl: string) => {
    const instances = [...settings.llm.ollama_instances];
    if (instances.length === 0) {
      const primary: OllamaInstance = {
        id: 'primary',
        name: 'Primary',
        base_url: newUrl,
        enabled: true,
        healthy: false,
      };
      instances.push(primary);
    } else {
      instances[0] = { ...instances[0], base_url: newUrl, enabled: true };
    }
    setSettings({ ...settings, llm: { ...settings.llm, ollama_instances: instances } });
    setVerification(null);
  };

  const handleVerifyOllamaUrl = async () => {
    if (!primaryOllamaUrl.trim()) return;
    setVerifyingUrl(true);
    try {
      const result = await settingsApi.verifyOllamaUrl(primaryOllamaUrl);
      setVerification(result);
    } catch {
      setVerification({ success: false, message: 'Verification request failed' });
    } finally {
      setVerifyingUrl(false);
    }
  };

  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 3, ...accentPaperSx('file') }}>
      <Typography
        variant="subtitle2"
        gutterBottom
        sx={{ display: 'flex', alignItems: 'center', gap: 1 }}
      >
        <MemoryIcon sx={{ fontSize: 18, color: ACCENT_COLORS.file }} />
        Embedding Provider
      </Typography>
      <Typography
        variant="body2"
        gutterBottom
        sx={{ color: 'text.secondary', mb: 2 }}
      >
        Configure the embedding provider and model for semantic search and vector indexing.
        Changing provider or model requires rebuilding search indexes.
      </Typography>

      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'flex-start', mb: 2 }}>
        <TextField
          select
          label="Provider"
          variant="outlined"
          value={provider}
          onChange={(e) => handleProviderChange(e.target.value)}
          sx={{ minWidth: 200 }}
        >
          {EMBEDDING_PROVIDERS.map((p) => (
            <MenuItem key={p.value} value={p.value}>
              {p.label}
            </MenuItem>
          ))}
        </TextField>

        <EmbeddingModelSelector
          provider={provider}
          model={settings.embedding?.model || ''}
          onModelChange={(model, dimensions) => {
            const updated = {
              ...settings,
              embedding: { ...settings.embedding, model },
            };
            if (dimensions !== undefined) {
              updated.search = { ...settings.search, vector_dimensions: dimensions };
            }
            setSettings(updated);
          }}
        />

        <TextField
          label="Dimensions"
          variant="outlined"
          type="number"
          value={settings.search.vector_dimensions}
          onChange={(e) =>
            setSettings({
              ...settings,
              search: {
                ...settings.search,
                vector_dimensions: parseInt(e.target.value) || 768,
              },
            })
          }
          sx={{ width: 150, ...numberInputSx }}
          slotProps={{ htmlInput: { min: 128, max: 4096 } }}
          helperText="Vector size"
        />
      </Box>

      {isOllama && (
        <Box sx={{ mb: 2 }}>
          <OllamaUrlField
            url={primaryOllamaUrl}
            onChange={handleOllamaUrlChange}
            verification={verification}
            onVerify={handleVerifyOllamaUrl}
            verifying={verifyingUrl}
            onClearVerification={() => setVerification(null)}
          />
        </Box>
      )}

      {isCloud && (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'flex-start', mb: 2 }}>
          <TextField
            label="API Key"
            variant="outlined"
            fullWidth
            type="password"
            value={
              settings.embedding?.api_key === 'configured'
                ? ''
                : (settings.embedding?.api_key || '')
            }
            placeholder={
              settings.embedding?.api_key === 'configured'
                ? 'API key configured (enter new value to change)'
                : undefined
            }
            onChange={(e) => updateEmbedding('api_key', e.target.value)}
            helperText={`${provider === 'openai' ? 'OpenAI' : 'Gemini'} API key for embeddings (independent from chat)`}
          />
          <TextField
            label="API Base URL (optional)"
            variant="outlined"
            fullWidth
            value={settings.embedding?.api_base || ''}
            onChange={(e) => updateEmbedding('api_base', e.target.value)}
            helperText="Custom endpoint override"
          />
        </Box>
      )}
    </Paper>
  );
}
