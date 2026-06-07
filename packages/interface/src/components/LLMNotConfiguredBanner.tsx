// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * LLMNotConfiguredBanner
 *
 * Persistent reminder shown above the page content when the LLM is not
 * yet usable. Two variants:
 *
 *   1. verified=false  → "Configure / verify your LLM" — provider URL or
 *      API key isn't populated yet, or no Test click on record.
 *   2. verified=true AND missing_models.length > 0 → "Pull these models"
 *      — the configured Ollama chat / extraction / vision model isn't
 *      present on any reachable instance.
 *
 * Mirrors the server-side gate at POST /sources + chat-send (both 409
 * with LLM_NOT_VERIFIED or EXTRACTION_MODEL_MISSING respectively) so the
 * banner and the gate cannot drift.
 */

import { Alert, Box, Link } from '@mui/material';
import { Link as RouterLink } from 'react-router';
import { useLLMHealth } from '../hooks/useLLMHealth';

export default function LLMNotConfiguredBanner() {
  const { data: health, isLoading } = useLLMHealth();

  if (isLoading || !health) {
    return null;
  }

  if (!health.verified) {
    return (
      <Box sx={{ mb: 2 }}>
        <Alert severity="warning" variant="outlined">
          {health.configured
            ? `Your ${health.provider} connection hasn't been verified — import and chat are disabled. `
            : `Configure your LLM provider to enable import and chat. `}
          <Link component={RouterLink} to="/settings?tab=llm" underline="always">
            open LLM settings
          </Link>
        </Alert>
      </Box>
    );
  }

  const missing = health.missing_models ?? [];
  if (missing.length > 0) {
    return (
      <Box sx={{ mb: 2 }}>
        <Alert severity="warning" variant="outlined">
          Configured {health.provider} model{missing.length > 1 ? 's' : ''}{' '}
          not pulled: <strong>{missing.join(', ')}</strong>. Import and chat
          are disabled until {missing.length > 1 ? 'they are' : 'it is'} pulled.{' '}
          <Link component={RouterLink} to="/settings?tab=llm" underline="always">
            open LLM settings
          </Link>
        </Alert>
      </Box>
    );
  }

  return null;
}
