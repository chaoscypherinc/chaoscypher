// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Source Info Tooltip Components
 *
 * Tooltip content for source info icons, showing metadata like type, size,
 * processing times, and LLM performance stats. Two variants: one for
 * non-active sources (basic info) and one for active/completed sources
 * (with full processing and LLM statistics).
 */

import { Box, Typography } from '@mui/material';
import type { UnifiedSource } from '../../../types';
import { formatFileSize, formatCompactNumber , formatDurationNullable } from '../../../utils/formatters';
import { StatusColors } from '../../../theme/colors';

/** Format full date + time for tooltip display. */
function formatFullDateTime(dateString: string): string {
  return new Date(dateString).toLocaleString('en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

interface SourceInfoTooltipProps {
  /** The source to display info for. */
  source: UnifiedSource;
}

/**
 * Tooltip content for non-active sources (processing/queued/error).
 *
 * Shows basic metadata: type, size, creation date, and chunk/embedding info
 * when available.
 */
export function SourceInfoTooltip({ source }: SourceInfoTooltipProps) {
  const ing = source.ingestion;
  const embeddingModel = ing?.embedding_model;
  const embeddingDims = ing?.embedding_dimensions;
  const chunksCount = ing?.chunks_count;

  return (
    <Box sx={{ p: 0.5, minWidth: 200 }}>
      <Typography variant="caption" sx={{ display: 'block' }}>
        <strong>Type:</strong> {source.source_type.toUpperCase()}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block' }}>
        <strong>Size:</strong> {formatFileSize(source.size)}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block' }}>
        <strong>Created:</strong> {formatFullDateTime(source.created_at)}
      </Typography>
      {chunksCount !== undefined && chunksCount > 0 && (
        <>
          <Box sx={{ borderTop: '1px solid', borderColor: 'divider', my: 0.5 }} />
          <Typography variant="caption" sx={{ display: 'block' }}>
            <strong>Chunks:</strong> {chunksCount}
          </Typography>
          {embeddingModel && (
            <Typography variant="caption" sx={{ display: 'block' }}>
              <strong>Model:</strong> {embeddingModel}
              {embeddingDims && ` (${embeddingDims}d)`}
            </Typography>
          )}
        </>
      )}
    </Box>
  );
}

/**
 * Tooltip content for active/completed sources (green checkmark icon).
 *
 * Shows full metadata including processing time breakdown (indexing,
 * extraction, commit) and LLM performance statistics (success rates,
 * token usage).
 */
export function ActiveSourceTooltip({ source }: SourceInfoTooltipProps) {
  const act = source.active;
  const isEnabled = act?.enabled !== false;
  const embeddingModel = act?.embedding_model;
  const embeddingDims = act?.embedding_dimensions;

  // Time breakdown
  const indexingDuration = act?.indexing_duration_seconds || 0;
  const extractionDuration = act?.extraction_duration_seconds || 0;
  const commitDuration = act?.commit_duration_seconds || 0;
  const totalDuration = indexingDuration + extractionDuration + commitDuration;
  const formattedDuration = formatDurationNullable(totalDuration);

  // LLM stats
  const llmTotalCalls = act?.llm_total_calls;
  const permanentFailures = act?.llm_permanent_failures ?? 0;
  const firstTry = act?.llm_first_try_successes ?? 0;
  const retries = act?.llm_retry_successes ?? 0;
  const inputTokens = act?.llm_total_input_tokens ?? 0;
  const outputTokens = act?.llm_total_output_tokens ?? 0;
  const model = act?.llm_model;
  const extractionMode = act?.extraction_mode;

  return (
    <Box sx={{ p: 0.5, minWidth: 200 }}>
      {/* Basic Info */}
      <Typography variant="caption" sx={{ display: 'block' }}>
        <strong>Type:</strong> {source.source_type.toUpperCase()}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block' }}>
        <strong>Size:</strong> {formatFileSize(source.size)}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block' }}>
        <strong>Status:</strong> {isEnabled ? 'Active' : 'Disabled'}
      </Typography>
      <Typography variant="caption" sx={{ display: 'block' }}>
        <strong>Created:</strong> {formatFullDateTime(source.created_at)}
      </Typography>
      {/* Embedding model */}
      {embeddingModel && (
        <Typography variant="caption" sx={{ display: 'block' }}>
          <strong>Embeddings:</strong> {embeddingModel}
          {embeddingDims && ` (${embeddingDims}d)`}
        </Typography>
      )}
      {/* Processing time */}
      {formattedDuration && (
        <>
          <Box sx={{ borderTop: '1px solid', borderColor: 'divider', my: 0.5 }} />
          <Typography
            variant="caption"
            sx={{ display: 'block', fontWeight: 600, mb: 0.25 }}
          >
            Processing: {formattedDuration}
          </Typography>
          {formatDurationNullable(indexingDuration) && (
            <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
              Indexing: {formatDurationNullable(indexingDuration)}
            </Typography>
          )}
          {formatDurationNullable(extractionDuration) && (
            <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
              Extraction: {formatDurationNullable(extractionDuration)}
            </Typography>
          )}
          {formatDurationNullable(commitDuration) && (
            <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
              Import: {formatDurationNullable(commitDuration)}
            </Typography>
          )}
        </>
      )}
      {/* LLM Performance */}
      {llmTotalCalls != null && llmTotalCalls > 0 && (
        <>
          <Box sx={{ borderTop: '1px solid', borderColor: 'divider', my: 0.5 }} />
          <Typography
            variant="caption"
            sx={{ display: 'block', fontWeight: 600, mb: 0.25 }}
          >
            LLM {model && <span style={{ fontWeight: 'normal' }}>({model})</span>}
            {extractionMode && (
              <span style={{ fontWeight: 'normal' }}> [{extractionMode.toUpperCase()}]</span>
            )}
          </Typography>
          <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
            First-try: {firstTry} | Retries: {retries}
            {permanentFailures > 0 && (
              <span style={{ color: StatusColors.failed }}> | Failed: {permanentFailures}</span>
            )}
          </Typography>
          <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
            Tokens: {formatCompactNumber(inputTokens)} in / {formatCompactNumber(outputTokens)} out
          </Typography>
        </>
      )}
    </Box>
  );
}
