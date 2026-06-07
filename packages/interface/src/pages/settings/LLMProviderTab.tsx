// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  Typography,
  FormControlLabel,
  Alert,
  Checkbox,
  CircularProgress,
  Switch,
  Tooltip,
} from '@mui/material';
import type { Settings } from '../../types';
import { useProviderSettings } from './hooks/useProviderSettings';
import ProviderSelector from './components/ProviderSelector';
import InstanceManager from './components/InstanceManager';
import ModelSelector from './components/ModelSelector';
import VRAMPresets from './components/VRAMPresets';
import ToolApprovalAccordion from './ToolApprovalAccordion';

interface LLMProviderTabProps {
  settings: Settings;
  setSettings: (settings: Settings) => void;
  /**
   * Hide the "Show Advanced Settings" checkbox at the bottom of the tab
   * and force-collapse advanced sections. Used by the first-run wizard so
   * brand-new users aren't faced with multi-instance management, VRAM
   * accordions, and thinking-mode toggles before they've configured a
   * single model. Default false: Settings page sees the full UI.
   */
  hideAdvancedToggle?: boolean;
}

export default function LLMProviderTab({
  settings,
  setSettings,
  hideAdvancedToggle = false,
}: LLMProviderTabProps) {
  const {
    newInstance,
    setNewInstance,
    presets,
    applyingPreset,
    presetMessage,
    setPresetMessage,
    showAdvanced,
    setShowAdvanced,
    verifyingUrl,
    urlVerification,
    clearUrlVerification,
    cloudModels,
    ollamaInstances,
    enabledInstanceCount,
    primaryOllamaUrl,
    currentPreset,
    handleApplyPreset,
    handleVerifyOllamaUrl,
    handleUrlChange,
    handleAddInstance,
    handleRemoveInstance,
    handleToggleInstance,
    handleChatProviderChange,
  } = useProviderSettings(settings, setSettings);

  // The wizard suppresses the advanced toggle entirely, but `showAdvanced`
  // could already be true if the user toggled it on the Settings page in a
  // prior session and the state persisted. Force it to false in wizard
  // mode so the advanced sections collapse regardless of stored state.
  const advancedExpanded = hideAdvancedToggle ? false : showAdvanced;

  // Tool-call approval is provider-agnostic but lives inside the model config:
  // under the configuration summary, above the multiple-instance / load-
  // balancing settings (Ollama). Only shown under Advanced Settings.
  const toolApprovalSection = advancedExpanded ? (
    <ToolApprovalAccordion settings={settings} setSettings={setSettings} />
  ) : null;

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h6" gutterBottom>
        LLM Provider Configuration
      </Typography>
      {/* Provider Setup Section */}
      <ProviderSelector
        settings={settings}
        setSettings={setSettings}
        presets={presets}
        applyingPreset={applyingPreset}
        presetMessage={presetMessage}
        setPresetMessage={setPresetMessage}
        showAdvanced={advancedExpanded}
        urlVerification={urlVerification}
        verifyingUrl={verifyingUrl}
        onApplyPreset={handleApplyPreset}
        onVerifyOllamaUrl={handleVerifyOllamaUrl}
        onUrlChange={handleUrlChange}
        onClearVerification={clearUrlVerification}
        primaryOllamaUrl={primaryOllamaUrl}
        onChatProviderChange={handleChatProviderChange}
      />
      {/* Model Configuration */}
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {/* Ollama Configuration */}
        {settings.llm.chat_provider === 'ollama' && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {/* Model Selection Summary Card */}
            <ModelSelector
              settings={settings}
              setSettings={setSettings}
              showAdvanced={advancedExpanded}
              currentPreset={currentPreset}
              cloudModels={cloudModels}
            />

            {!currentPreset && presets.length > 0 && (
              <Alert severity="info">
                Select your GPU VRAM above to configure optimal model settings automatically.
              </Alert>
            )}

            {applyingPreset && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <CircularProgress size={20} />
                <Typography variant="body2" sx={{
                  color: "text.secondary"
                }}>
                  Applying preset...
                </Typography>
              </Box>
            )}

            {/* Advanced Options - only visible when toggled */}
            {advancedExpanded && (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {/* Tool-call approval — above multiple instances / load balancing */}
                {toolApprovalSection}

                {/* Multiple Instances & Load Balancing */}
                <InstanceManager
                  settings={settings}
                  setSettings={setSettings}
                  ollamaInstances={ollamaInstances}
                  enabledInstanceCount={enabledInstanceCount}
                  newInstance={newInstance}
                  setNewInstance={setNewInstance}
                  onAddInstance={handleAddInstance}
                  onRemoveInstance={handleRemoveInstance}
                  onToggleInstance={handleToggleInstance}
                />

                {/* Performance, LLM, Queue, Embedding Accordions */}
                <VRAMPresets
                  settings={settings}
                  setSettings={setSettings}
                />

                {/* Thinking mode toggle */}
                <Tooltip title="When enabled, the model reasons through complex questions before answering, improving quality for knowledge graph queries. Requires a model that supports thinking (e.g. Qwen3, DeepSeek-R1)." arrow placement="right">
                  <FormControlLabel
                    control={
                      <Switch
                        checked={settings.llm.thinking_for_chat ?? false}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            llm: { ...settings.llm, thinking_for_chat: e.target.checked },
                          })
                        }
                      />
                    }
                    label="Enable thinking for chat"
                  />
                </Tooltip>
              </Box>
            )}
          </Box>
        )}

        {/* Cloud Provider Configurations */}
        {settings.llm.chat_provider !== 'ollama' && (
          <>
            <ModelSelector
              settings={settings}
              setSettings={setSettings}
              showAdvanced={advancedExpanded}
              currentPreset={currentPreset}
              cloudModels={cloudModels}
            />
            {/* Tool-call approval — under the configuration summary (cloud
                providers have no multiple-instance settings) */}
            {toolApprovalSection}
          </>
        )}
      </Box>
      {/* Advanced toggle — Settings page only. The first-run wizard hides
          this so brand-new users aren't shown instance managers, VRAM
          accordions, and thinking-mode toggles before they've finished
          their first model pick. */}
      {!hideAdvancedToggle && (
        <FormControlLabel
          control={
            <Checkbox
              checked={showAdvanced}
              onChange={(e) => setShowAdvanced(e.target.checked)}
            />
          }
          label="Show Advanced Settings"
          sx={{ mt: 1 }}
        />
      )}
    </Box>
  );
}
