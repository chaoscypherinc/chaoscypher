// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import {
  Box,
  Typography,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  TextField,
  Alert,
  Chip,
  FormHelperText,
  Button,
  CircularProgress,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutlined';
import SettingsIcon from '@mui/icons-material/Settings';
import type { Settings, VRAMPreset, OllamaVerifyResponse, LLMProvider, LLMVerifyResponse } from '../../../types';
import { settingsApi } from '../../../services/api/settings';
import { accentPaperSx, ACCENT_COLORS } from '../../../theme/accentStyles';
import { OllamaUrlField } from '../../../components/settings';

/** Check if a value is a masked secret placeholder from the API.
 *
 * The backend returns ``"configured"`` (set) or ``null`` (unset) — never a
 * partial reveal.  We detect the placeholder by exact equality so that a
 * user who genuinely types the word "configured" as their API key is not
 * silently discarded.
 */
const isMaskedSecret = (value: string | null | undefined): boolean =>
  value === 'configured';

/** Inline Test button for a cloud provider API key field.
 *
 * Calls the cloud-LLM verify endpoint; the backend records a successful
 * result into the in-memory verify tracker, which clears the action-gate
 * banner. Disabled when the key is empty or still showing the masked
 * "configured" placeholder — only fresh-typed keys can be probed since
 * the verify endpoint needs the raw value.
 */
function CloudKeyTester({ provider, apiKey }: { provider: LLMProvider; apiKey: string }) {
  const [state, setState] = useState<'idle' | 'testing' | LLMVerifyResponse>('idle');
  const canTest = apiKey.length > 0 && !isMaskedSecret(apiKey);

  const handleTest = async () => {
    setState('testing');
    try {
      const result = await settingsApi.verifyLLM(provider, apiKey);
      setState(result);
    } catch {
      setState({ success: false, message: 'Test request failed', provider });
    }
  };

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5 }}>
      <Button
        size="small"
        variant="outlined"
        disabled={!canTest || state === 'testing'}
        onClick={handleTest}
        startIcon={state === 'testing' ? <CircularProgress size={14} /> : undefined}
      >
        {state === 'testing' ? 'Testing…' : 'Test connection'}
      </Button>
      {typeof state === 'object' && state.success && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, color: 'success.main' }}>
          <CheckCircleIcon fontSize="small" />
          <Typography variant="caption">{state.message}</Typography>
        </Box>
      )}
      {typeof state === 'object' && !state.success && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, color: 'error.main' }}>
          <ErrorOutlineIcon fontSize="small" />
          <Typography variant="caption">{state.message}</Typography>
        </Box>
      )}
      {!canTest && state === 'idle' && (
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          {isMaskedSecret(apiKey)
            ? 'Re-enter the key to test'
            : 'Enter a key to test'}
        </Typography>
      )}
    </Box>
  );
}

interface ProviderSelectorProps {
  /** Current application settings. */
  settings: Settings;
  /** Callback to update settings. */
  setSettings: (settings: Settings) => void;
  /** Available VRAM presets for Ollama. */
  presets: VRAMPreset[];
  /** Whether a preset is currently being applied. */
  applyingPreset: boolean;
  /** Message to display after applying a preset. */
  presetMessage: { type: 'success' | 'error' | 'warning'; text: string } | null;
  /** Clear the preset message. */
  setPresetMessage: (value: { type: 'success' | 'error' | 'warning'; text: string } | null) => void;
  /** Whether advanced options are shown. */
  showAdvanced: boolean;
  /** Ollama URL verification state. */
  urlVerification: OllamaVerifyResponse | null;
  /** Whether the URL is currently being verified. */
  verifyingUrl: boolean;
  /** Handler to apply a VRAM preset. */
  onApplyPreset: (presetId: string) => Promise<void>;
  /** Handler to verify the Ollama URL. */
  onVerifyOllamaUrl: () => Promise<void>;
  /** Handler when the Ollama URL changes. */
  onUrlChange: (newUrl: string) => void;
  /** Handler to clear URL verification. */
  onClearVerification: () => void;
  /** URL of the primary Ollama instance (instances[0].base_url). */
  primaryOllamaUrl: string;
  /** Switch chat provider and seed the recommended chat/extraction/vision models. */
  onChatProviderChange: (newProvider: string) => void;
}

/**
 * Provider selection UI including chat provider dropdown,
 * provider-specific API key fields, and Ollama URL/VRAM preset configuration.
 */
export default function ProviderSelector({
  settings,
  setSettings,
  presets,
  applyingPreset,
  presetMessage,
  setPresetMessage,
  showAdvanced,
  urlVerification,
  verifyingUrl,
  onApplyPreset,
  onVerifyOllamaUrl,
  onUrlChange,
  onClearVerification,
  primaryOllamaUrl,
  onChatProviderChange,
}: ProviderSelectorProps) {
  return (
    <Box sx={{ p: 2, mb: 2, ...accentPaperSx('file') }}>
      <Typography variant="subtitle2" sx={{ mb: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
        <SettingsIcon sx={{ fontSize: 18, color: ACCENT_COLORS.file }} />
        Provider Setup
      </Typography>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <FormControl fullWidth variant="outlined">
          <InputLabel>Chat Provider</InputLabel>
          <Select
            value={settings.llm.chat_provider}
            label="Chat Provider"
            onChange={(e) => onChatProviderChange(e.target.value)}
          >
            <MenuItem value="ollama">Ollama (Local)</MenuItem>
            <MenuItem value="openai">OpenAI</MenuItem>
            <MenuItem value="anthropic">Anthropic (Claude)</MenuItem>
            <MenuItem value="gemini">Google Gemini</MenuItem>
          </Select>
        </FormControl>

        {/* Ollama Settings - URL and VRAM */}
        {settings.llm.chat_provider === 'ollama' && (
          <>
            {/* Preset Message */}
            {presetMessage && (
              <Alert severity={presetMessage.type} onClose={() => setPresetMessage(null)}>
                {presetMessage.text}
              </Alert>
            )}

            {/* Ollama URL (extracted component) */}
            <OllamaUrlField
              url={primaryOllamaUrl}
              onChange={onUrlChange}
              verification={urlVerification}
              onVerify={onVerifyOllamaUrl}
              verifying={verifyingUrl}
              onClearVerification={onClearVerification}
            />

            {/* VRAM Preset Selection */}
            <FormControl fullWidth variant="outlined">
              <InputLabel>GPU VRAM</InputLabel>
              <Select
                value={settings.llm.ollama_quick_preset || ''}
                label="GPU VRAM"
                onChange={(e) => onApplyPreset(e.target.value)}
                disabled={applyingPreset}
              >
                {presets.map((preset) => (
                  <MenuItem key={preset.name} value={preset.name}>
                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: 1 }}>
                      <Box sx={{ minWidth: 0 }}>
                        <Typography sx={{
                          fontWeight: "medium"
                        }}>{preset.display_name}</Typography>
                        <Typography variant="caption" sx={{
                          color: "text.secondary"
                        }}>
                          {preset.gpu_examples.slice(0, 3).join(', ')}
                          {preset.gpu_examples.length > 3 && '...'}
                        </Typography>
                      </Box>
                      <Chip
                        size="small"
                        label={preset.ollama_settings.ollama_chat_model}
                        variant="outlined"
                        sx={{ flexShrink: 0 }}
                      />
                    </Box>
                  </MenuItem>
                ))}
              </Select>
              <FormHelperText>
                {showAdvanced
                  ? "Select preset to populate defaults (customize below)"
                  : "Select your GPU's VRAM for optimal settings"}
              </FormHelperText>
            </FormControl>
          </>
        )}

        {/* OpenAI Settings - API Key and URL */}
        {settings.llm.chat_provider === 'openai' && (
          <>
            <TextField
              label="OpenAI API Key"
              type="password"
              variant="outlined"
              value={isMaskedSecret(settings.llm.openai_api_key) ? '' : (settings.llm.openai_api_key || '')}
              placeholder={isMaskedSecret(settings.llm.openai_api_key) ? 'API key configured (enter new value to change)' : undefined}
              onChange={(e) =>
                setSettings({ ...settings, llm: { ...settings.llm, openai_api_key: e.target.value } })
              }
              fullWidth
              helperText="Your OpenAI API key (starts with sk-)"
            />
            <CloudKeyTester provider="openai" apiKey={settings.llm.openai_api_key || ''} />
            <TextField
              label="Base URL (Optional)"
              variant="outlined"
              value={settings.llm.openai_base_url}
              onChange={(e) =>
                setSettings({ ...settings, llm: { ...settings.llm, openai_base_url: e.target.value } })
              }
              fullWidth
              helperText="Default: https://api.openai.com/v1 (change for compatible APIs)"
            />
          </>
        )}

        {/* Anthropic Settings - API Key */}
        {settings.llm.chat_provider === 'anthropic' && (
          <>
            <TextField
              label="Anthropic API Key"
              type="password"
              variant="outlined"
              value={isMaskedSecret(settings.llm.anthropic_api_key) ? '' : (settings.llm.anthropic_api_key || '')}
              placeholder={isMaskedSecret(settings.llm.anthropic_api_key) ? 'API key configured (enter new value to change)' : undefined}
              onChange={(e) =>
                setSettings({ ...settings, llm: { ...settings.llm, anthropic_api_key: e.target.value } })
              }
              fullWidth
              helperText="Your Anthropic API key (starts with sk-ant-)"
            />
            <CloudKeyTester provider="anthropic" apiKey={settings.llm.anthropic_api_key || ''} />
          </>
        )}

        {/* Gemini Settings - API Key */}
        {settings.llm.chat_provider === 'gemini' && (
          <>
            <TextField
              label="Gemini API Key"
              type="password"
              variant="outlined"
              value={isMaskedSecret(settings.llm.gemini_api_key) ? '' : (settings.llm.gemini_api_key || '')}
              placeholder={isMaskedSecret(settings.llm.gemini_api_key) ? 'API key configured (enter new value to change)' : undefined}
              onChange={(e) =>
                setSettings({ ...settings, llm: { ...settings.llm, gemini_api_key: e.target.value } })
              }
              fullWidth
              helperText="Your Google AI Studio API key"
            />
            <CloudKeyTester provider="gemini" apiKey={settings.llm.gemini_api_key || ''} />
          </>
        )}
      </Box>
    </Box>
  );
}
