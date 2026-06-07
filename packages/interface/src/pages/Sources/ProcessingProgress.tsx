// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  Paper,
  Typography,
  Chip,
  Stack,
  LinearProgress,
} from '@mui/material';
import ProcessingIcon from '@mui/icons-material/HourglassEmpty';
import type { UnifiedSource } from '../../types';
import { aggregateProgress } from './utils/progressCalculation';
import { formatDurationNullable } from '../../utils/formatters';
import { StageColors } from '../../theme/colors';

interface ProcessingProgressProps {
  sources: UnifiedSource[];
  queueStats: { estimated_completion_times_human?: { llm: string; operations: string } } | null;
}

/**
 * Parse a timestamp string as UTC.
 * Backend returns timestamps without timezone suffix, but they are UTC.
 */
function parseAsUTC(timestamp: string): Date {
  // If timestamp doesn't have timezone info, treat it as UTC
  if (!timestamp.endsWith('Z') && !timestamp.includes('+') && !timestamp.includes('-', 10)) {
    // Replace space with 'T' for ISO format and add 'Z' for UTC
    return new Date(timestamp.replace(' ', 'T') + 'Z');
  }
  return new Date(timestamp);
}

/**
 * Calculate elapsed time in seconds from the earliest start timestamp.
 */
function calculateElapsedSeconds(sources: UnifiedSource[]): number | null {
  let earliestStart: Date | null = null;

  for (const source of sources) {
    const ingestion = source.ingestion;
    if (!ingestion) continue;

    // Check indexing start time
    if (ingestion.indexing_started_at) {
      const startDate = parseAsUTC(ingestion.indexing_started_at);
      if (!earliestStart || startDate < earliestStart) {
        earliestStart = startDate;
      }
    }

    // Check extraction start time
    if (ingestion.extraction_started_at) {
      const startDate = parseAsUTC(ingestion.extraction_started_at);
      if (!earliestStart || startDate < earliestStart) {
        earliestStart = startDate;
      }
    }
  }

  if (!earliestStart) return null;

  const now = new Date();
  return Math.floor((now.getTime() - earliestStart.getTime()) / 1000);
}

export function ProcessingProgress({ sources, queueStats }: ProcessingProgressProps) {
  // Filter to only processing sources (not active, not queued without progress)
  const processingSources = sources.filter(
    s => s.stage === 'processing' ||
    (s.stage === 'queued' && ['indexing', 'vision_pending', 'extracting', 'mcp_extracting', 'committing'].includes(s.status))
  );

  // Count queued sources waiting to be processed (exclude error status)
  const queuedSources = sources.filter(
    s => s.stage === 'queued' &&
    s.status !== 'error' &&
    !['indexing', 'vision_pending', 'extracting', 'mcp_extracting', 'committing'].includes(s.status)
  );

  // Count failed sources
  const failedSources = sources.filter(s => s.status === 'error');

  // Only show panel when there's active processing or queued items
  // Failures are shown in the table's Status column - no need for duplicate panel
  if (processingSources.length === 0 && queuedSources.length === 0) {
    return null;
  }

  // Calculate weighted aggregate progress across all processing sources.
  // ``totalEstimatedSeconds`` rolls up each source's
  // ``estimatedRemainingSeconds`` — which now prefers the live
  // ``stage_progress.avg_ms`` EMA — so this header automatically tracks
  // the same number the per-row top-right slot shows.
  const { totalProgress, dominantStageLabel, totalEstimatedSeconds } =
    aggregateProgress(processingSources);

  // Calculate elapsed time from processing start
  const calculatedElapsedSeconds = calculateElapsedSeconds(processingSources);
  const elapsedSeconds = calculatedElapsedSeconds;

  // Queue stats still provide a fallback for the indexing phase of a
  // brand-new upload where no source-level stage_progress row exists
  // yet (the LLM stages haven't started). Once any source has live
  // stage_progress data, that wins.
  let estimatedTime = formatDurationNullable(totalEstimatedSeconds) || '';
  if (!estimatedTime) {
    const hasIndexing = processingSources.some(s => s.status === 'indexing');
    if (hasIndexing && queueStats?.estimated_completion_times_human?.operations) {
      estimatedTime = queueStats.estimated_completion_times_human.operations;
    }
  }

  const elapsedTime = formatDurationNullable(elapsedSeconds) || '';

  return (
    <Paper sx={{ p: 2, mb: 2, bgcolor: 'primary.50' }}>
      <Stack spacing={1.5}>
        {/* Header row */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between"
          }}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1
            }}>
            <ProcessingIcon color="primary" />
            <Typography variant="subtitle1" sx={{
              fontWeight: "medium"
            }}>
              Processing Documents
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            {processingSources.length > 0 && (
              <Chip
                label={`${processingSources.length} active`}
                size="small"
                color="primary"
              />
            )}
            {queuedSources.length > 0 && (
              <Chip
                label={`${queuedSources.length} queued`}
                size="small"
                variant="outlined"
              />
            )}
            {failedSources.length > 0 && (
              <Chip
                label={`${failedSources.length} failed`}
                size="small"
                color="error"
              />
            )}
          </Stack>
        </Box>

        {/* Progress bar - simple overall progress */}
        {processingSources.length > 0 && (
          <Box>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                mb: 0.5
              }}>
              <Typography variant="body2" sx={{
                color: "text.secondary"
              }}>
                {dominantStageLabel}
                {elapsedTime && ` • ${elapsedTime} elapsed`}
              </Typography>
              {estimatedTime && (
                <Typography variant="body2" sx={{
                  color: "text.secondary"
                }}>
                  ~{estimatedTime} remaining
                </Typography>
              )}
            </Box>
            <LinearProgress
              variant="determinate"
              value={totalProgress}
              sx={{
                height: 8,
                borderRadius: 1,
                bgcolor: StageColors.empty,
                '& .MuiLinearProgress-bar': {
                  borderRadius: 1,
                }
              }}
            />
          </Box>
        )}
      </Stack>
    </Paper>
  );
}
