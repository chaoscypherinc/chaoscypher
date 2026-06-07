// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useState } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  LinearProgress,
  Paper,
  Typography,
} from '@mui/material';
import ErrorIcon from '@mui/icons-material/Error';
import { useAppConfig } from '../../../../contexts/useAppConfig';
import type { Source } from '../../../../types';

interface McpExtractionBannerProps {
  source: Source;
  onReset: () => void;
  onFinalize: () => void;
}

/**
 * Progress banner for sources in the `mcp_extracting` state. Shows
 * chunks submitted / total, entities/relationships preview, last
 * activity, and recovery actions when extraction goes stale (>10 min
 * since last activity). Progress data is sourced from stage_progress.
 */
export function McpExtractionBanner({
  source,
  onReset,
  onFinalize,
}: McpExtractionBannerProps) {
  const config = useAppConfig();
  const STALE_THRESHOLD_MS = config.intervals_mcp_stale_threshold_ms;

  // Read progress from stage_progress (LLM stage progress facility)
  const mcp = source.stage_progress?.['mcp_extraction'];
  const chunksSubmitted = mcp?.processed ?? 0;
  const chunksTotal = mcp?.total ?? 0;
  const lastActivity = mcp?.last_activity ?? null;
  const entitiesPreview = (mcp?.extras?.['entities_preview'] as number | undefined) ?? 0;
  const relationshipsPreview = (mcp?.extras?.['relationships_preview'] as number | undefined) ?? 0;

  const progressPct = chunksTotal > 0 ? (chunksSubmitted / chunksTotal) * 100 : 0;
  // Tick once every 30s so the staleness comparison re-evaluates against
  // current wall-clock without making the comparison itself impure-during-render.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(interval);
  }, []);
  const isStale = lastActivity
    ? now - new Date(lastActivity).getTime() > STALE_THRESHOLD_MS
    : false;

  return (
    <Paper sx={{ p: 2, mb: 2, bgcolor: isStale ? 'warning.dark' : 'action.hover' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        {isStale
          ? <ErrorIcon color="warning" />
          : <CircularProgress size={20} sx={{ color: 'primary.main' }} />
        }
        <Box sx={{ flex: 1 }}>
          <Typography variant="body2" sx={{ fontWeight: 500 }}>
            MCP Entity Extraction {isStale && '(Stale)'}
          </Typography>
          <Box sx={{ mt: 1 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Chunks submitted: {chunksSubmitted} / {chunksTotal}
              </Typography>
              {chunksTotal > 0 && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  {Math.round(progressPct)}%
                </Typography>
              )}
            </Box>
            {chunksTotal > 0 && (
              <LinearProgress
                variant="determinate"
                value={progressPct}
                sx={{ height: 6, borderRadius: 1 }}
              />
            )}
            <Box sx={{ display: 'flex', gap: 3, mt: 1 }}>
              {entitiesPreview > 0 && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  Entities found: {entitiesPreview}
                </Typography>
              )}
              {relationshipsPreview > 0 && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  Relationships found: {relationshipsPreview}
                </Typography>
              )}
              {lastActivity && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  Last activity: {new Date(lastActivity).toLocaleTimeString()}
                </Typography>
              )}
            </Box>
          </Box>
          {isStale && (
            <Box sx={{ display: 'flex', gap: 1, mt: 1.5 }}>
              <Button size="small" variant="outlined" onClick={onReset}>
                Reset to Indexed
              </Button>
              <Button
                size="small"
                variant="outlined"
                color="primary"
                onClick={onFinalize}
              >
                Finalize Partial
              </Button>
            </Box>
          )}
        </Box>
      </Box>
    </Paper>
  );
}
