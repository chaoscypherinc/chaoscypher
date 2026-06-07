// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  CircularProgress,
  LinearProgress,
  Paper,
  Typography,
} from '@mui/material';
import type { Source } from '../../../../types';
import { formatDuration } from '../../../../utils/formatters';
import { getStatusLabel } from '../header/statusMeta';
import type { ExtractionProgress } from '../../hooks/useSourceDetail';

interface ProcessingBannerProps {
  source: Source;
  extractionProgress: ExtractionProgress | null;
}

/**
 * Progress banner shown during any `isSourceProcessing` state that is
 * NOT `mcp_extracting` (which has its own banner). Shows status
 * description, chunk progress bar, and per-chunk timing when
 * extraction progress data is available.
 */
export function ProcessingBanner({ source, extractionProgress }: ProcessingBannerProps) {
  return (
    <Paper sx={{ p: 2, mb: 2, bgcolor: 'action.hover' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        <CircularProgress size={20} sx={{ color: 'primary.main' }} />
        <Box sx={{ flex: 1 }}>
          <Typography variant="body2" sx={{ fontWeight: 500 }}>
            {source.step_description || `${getStatusLabel(source.status)}...`}
          </Typography>
          {extractionProgress && (
            <Box sx={{ mt: 1 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  Chunks: {extractionProgress.completed_chunks} / {extractionProgress.total_chunks}
                </Typography>
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  {Math.round(extractionProgress.progress_percent)}%
                </Typography>
              </Box>
              <LinearProgress
                variant="determinate"
                value={extractionProgress.progress_percent}
                sx={{ height: 6, borderRadius: 1 }}
              />
              {extractionProgress.timing && (
                <Box sx={{ display: 'flex', gap: 3, mt: 1 }}>
                  {extractionProgress.timing.elapsed_seconds != null && (
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                      Elapsed: {formatDuration(extractionProgress.timing.elapsed_seconds)}
                    </Typography>
                  )}
                  {extractionProgress.timing.avg_chunk_time_seconds != null && (
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                      Avg/chunk: {formatDuration(extractionProgress.timing.avg_chunk_time_seconds)}
                    </Typography>
                  )}
                  {extractionProgress.timing.estimated_remaining_seconds != null && (
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                      Remaining: ~{formatDuration(extractionProgress.timing.estimated_remaining_seconds)}
                    </Typography>
                  )}
                </Box>
              )}
              {extractionProgress.current_chunk && (
                <Box sx={{ display: 'flex', gap: 3, mt: 0.5 }}>
                  <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                    Current chunk: #{extractionProgress.current_chunk.chunk_index + 1}
                  </Typography>
                  {extractionProgress.current_chunk.elapsed_seconds != null && (
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                      Chunk time: {formatDuration(extractionProgress.current_chunk.elapsed_seconds)}
                    </Typography>
                  )}
                  {extractionProgress.current_chunk.retry_count > 0 && (
                    <Typography variant="caption" sx={{ color: 'warning.main' }}>
                      Retry {extractionProgress.current_chunk.retry_count}/{extractionProgress.current_chunk.max_retries}
                    </Typography>
                  )}
                </Box>
              )}
            </Box>
          )}
        </Box>
      </Box>
    </Paper>
  );
}
