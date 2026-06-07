// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  Typography,
  TextField,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Link,
  FormControlLabel,
  Checkbox,
  Divider,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import SpeedIcon from '@mui/icons-material/Speed';
import PsychologyIcon from '@mui/icons-material/Psychology';
import QueueIcon from '@mui/icons-material/Queue';
import type { Settings } from '../../../types';
import { accentAccordionSx } from '../../../theme/settings';
import { ACCENT_COLORS } from '../../../theme/accentStyles';

interface VRAMPresetsProps {
  /** Current application settings. */
  settings: Settings;
  /** Callback to update settings. */
  setSettings: (settings: Settings) => void;
}

/**
 * Ollama-specific advanced configuration accordions.
 *
 * Includes performance tuning, general LLM settings,
 * queue & priority settings, and advanced embedding settings.
 */
export default function VRAMPresets({
  settings,
  setSettings,
}: VRAMPresetsProps) {
  return (
    <>
      {/* Performance Tuning */}
      <Accordion sx={accentAccordionSx('domain')}>
        <AccordionSummary
          expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.domain }} />}
          sx={{ '&:hover': { bgcolor: 'transparent' }, minHeight: 56 }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <SpeedIcon sx={{ fontSize: 18, color: ACCENT_COLORS.domain }} />
            <Typography variant="subtitle2" sx={{
              fontWeight: "medium"
            }}>
              Performance Tuning
            </Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <Alert severity="info" sx={{ mb: 1 }}>
              Advanced Ollama parameters. Leave empty to use model defaults.{' '}
              <Link href="https://github.com/ollama/ollama/blob/main/docs/modelfile.md" target="_blank" rel="noopener">
                Learn more
              </Link>
            </Alert>

            <TextField
              label="Batch Size (num_batch)"
              type="number"
              value={settings.llm.ollama_num_batch || ''}
              onChange={(e) =>
                setSettings({ ...settings, llm: { ...settings.llm, ollama_num_batch: e.target.value ? parseInt(e.target.value) : undefined } })
              }
              fullWidth
              helperText="Batch size for processing"
              slotProps={{ htmlInput: { min: 128, max: 8192, step: 128 } }}
            />

            <TextField
              label="Parallel Sequences (num_parallel)"
              type="number"
              value={settings.llm.ollama_num_parallel || ''}
              onChange={(e) =>
                setSettings({ ...settings, llm: { ...settings.llm, ollama_num_parallel: e.target.value ? parseInt(e.target.value) : undefined } })
              }
              fullWidth
              helperText="Number of parallel sequences"
              slotProps={{ htmlInput: { min: 1, max: 16 } }}
            />

            <TextField
              label="CPU Threads (num_thread)"
              type="number"
              value={settings.llm.ollama_num_thread || ''}
              onChange={(e) =>
                setSettings({ ...settings, llm: { ...settings.llm, ollama_num_thread: e.target.value ? parseInt(e.target.value) : undefined } })
              }
              fullWidth
              helperText="CPU threads to use"
              slotProps={{ htmlInput: { min: 1, max: 64 } }}
            />
          </Box>
        </AccordionDetails>
      </Accordion>
      {/* General LLM Settings */}
      <Accordion sx={accentAccordionSx('domain')}>
        <AccordionSummary
          expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.domain }} />}
          sx={{ '&:hover': { bgcolor: 'transparent' }, minHeight: 56 }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <PsychologyIcon sx={{ fontSize: 18, color: ACCENT_COLORS.domain }} />
            <Typography variant="subtitle2" sx={{
              fontWeight: "medium"
            }}>
              General LLM Settings
            </Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <Alert severity="info" sx={{ mb: 1 }}>
              Configure AI behavior, cost tracking, and output limits.
            </Alert>

            {/* Cost Tracking */}
            <FormControlLabel
              control={
                <Checkbox
                  checked={settings.llm.enable_token_cost_tracking}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      llm: {
                        ...settings.llm,
                        enable_token_cost_tracking: e.target.checked,
                      },
                    })
                  }
                />
              }
              label="Cost Tracking"
            />
            <Typography
              variant="caption"
              sx={{
                color: "text.secondary",
                ml: 4,
                mt: -1
              }}>
              Track token usage and estimated costs
            </Typography>
            {settings.llm.enable_token_cost_tracking && (
              <Box sx={{ display: 'flex', gap: 1, ml: 4 }}>
                <TextField
                  label="Input $/1M"
                  type="number"
                  size="small"
                  variant="outlined"
                  value={settings.llm.token_cost_input_per_million}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      llm: {
                        ...settings.llm,
                        token_cost_input_per_million: parseFloat(e.target.value) || 0,
                      },
                    })
                  }
                  sx={{
                    width: 100,
                    '& input::-webkit-outer-spin-button, & input::-webkit-inner-spin-button': { display: 'none' },
                    '& input[type=number]': { MozAppearance: 'textfield' },
                  }}
                  slotProps={{ htmlInput: { min: 0, step: 0.01 } }}
                />
                <TextField
                  label="Output $/1M"
                  type="number"
                  size="small"
                  variant="outlined"
                  value={settings.llm.token_cost_output_per_million}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      llm: {
                        ...settings.llm,
                        token_cost_output_per_million: parseFloat(e.target.value) || 0,
                      },
                    })
                  }
                  sx={{
                    width: 100,
                    '& input::-webkit-outer-spin-button, & input::-webkit-inner-spin-button': { display: 'none' },
                    '& input[type=number]': { MozAppearance: 'textfield' },
                  }}
                  slotProps={{ htmlInput: { min: 0, step: 0.01 } }}
                />
              </Box>
            )}

            <Divider />

            <TextField
              label="Temperature"
              type="number"
              value={settings.llm.ai_temperature}
              onChange={(e) =>
                setSettings({ ...settings, llm: { ...settings.llm, ai_temperature: parseFloat(e.target.value) } })
              }
              fullWidth
              helperText="0.0 = deterministic, 2.0 = very creative (default: 0.7)"
              slotProps={{ htmlInput: { min: 0, max: 2, step: 0.1 } }}
            />

            <TextField
              label="Max Tokens (Output)"
              type="number"
              value={settings.llm.ai_max_tokens}
              onChange={(e) =>
                setSettings({ ...settings, llm: { ...settings.llm, ai_max_tokens: parseInt(e.target.value) } })
              }
              fullWidth
              helperText="Maximum tokens AI can generate"
              slotProps={{ htmlInput: { min: 256, max: 32768, step: 256 } }}
            />

          </Box>
        </AccordionDetails>
      </Accordion>
      {/* Queue Settings */}
      <Accordion sx={accentAccordionSx('domain')}>
        <AccordionSummary
          expandIcon={<ExpandMoreIcon sx={{ color: ACCENT_COLORS.domain }} />}
          sx={{ '&:hover': { bgcolor: 'transparent' }, minHeight: 56 }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <QueueIcon sx={{ fontSize: 18, color: ACCENT_COLORS.domain }} />
            <Typography variant="subtitle2" sx={{
              fontWeight: "medium"
            }}>
              Queue & Priority Settings
            </Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <Alert severity="info" sx={{ mb: 1 }}>
              Controls how LLM requests are prioritized and queued.
            </Alert>

            <TextField
              label="Max Concurrent LLM Requests"
              type="number"
              value={settings.llm.llm_max_concurrent || 1}
              onChange={(e) => {
                const newMaxConcurrent = parseInt(e.target.value) || 1;
                const newReserved = Math.min(
                  settings.llm.llm_reserved_interactive || 0,
                  newMaxConcurrent - 1
                );
                setSettings({
                  ...settings,
                  llm: {
                    ...settings.llm,
                    llm_max_concurrent: newMaxConcurrent,
                    llm_reserved_interactive: Math.max(0, newReserved)
                  }
                });
              }}
              fullWidth
              helperText="Total concurrent LLM requests (1 for local Ollama, higher for cloud)"
              slotProps={{ htmlInput: { min: 1, max: 10 } }}
            />

            <TextField
              label="Reserved Slots for Interactive Chat"
              type="number"
              value={settings.llm.llm_reserved_interactive || 0}
              onChange={(e) => {
                const maxConcurrent = settings.llm.llm_max_concurrent || 1;
                const maxReserved = maxConcurrent - 1;
                const newValue = Math.min(parseInt(e.target.value) || 0, maxReserved);
                setSettings({ ...settings, llm: { ...settings.llm, llm_reserved_interactive: Math.max(0, newValue) } });
              }}
              fullWidth
              helperText={`Reserved for chat (max ${Math.max(0, (settings.llm.llm_max_concurrent || 1) - 1)})`}
              slotProps={{
                htmlInput: { min: 0, max: Math.max(0, (settings.llm.llm_max_concurrent || 1) - 1) }
              }}
            />

            <TextField
              label="Max Retries"
              type="number"
              value={settings.llm.llm_max_retries || 3}
              onChange={(e) =>
                setSettings({ ...settings, llm: { ...settings.llm, llm_max_retries: parseInt(e.target.value) || 3 } })
              }
              fullWidth
              helperText="Retry failed LLM operations"
              slotProps={{ htmlInput: { min: 0, max: 10 } }}
            />
          </Box>
        </AccordionDetails>
      </Accordion>
    </>
  );
}
