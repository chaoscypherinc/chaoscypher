// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Divider, Typography } from '@mui/material';
import type { Source } from '../../../../types';
import { formatFileSize, formatDate } from '../../../../utils/formatters';

interface FileInfoTooltipProps {
  source: Source;
}

function describeNormalization(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return 'File-type default';
  return value ? 'On' : 'Off';
}

/**
 * Tooltip body shown when the user hovers the source title (or the
 * domain chip).
 *
 * Contents:
 *   1. File provenance — filename, type, size, created_at, model
 *      choices that were used at extraction time.
 *   2. Upload settings — the user's choices at upload time
 *      (``SourceResponse.upload_options``). Moved here on 2026-05-11
 *      from a standalone glass panel under the stat tiles so the page
 *      isn't dominated by name/value lists the operator only consults
 *      occasionally.
 *
 * **When adding a new persisted upload setting:** add it to the lower
 * "Upload settings" block below as well — this is now the surface the
 * UI standard names as the upload-time-choices presentation, per
 * `packages/interface/CLAUDE.md` § "Source detail page anatomy".
 */
export function FileInfoTooltip({ source }: FileInfoTooltipProps) {
  const opts = source.upload_options;
  return (
    <Box sx={{ p: 0.5, maxWidth: 360 }}>
      <Typography
        variant="caption"
        sx={{ fontWeight: 600, display: 'block', mb: 0.5 }}
      >
        File Information
      </Typography>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
        <Typography variant="caption">Filename: {source.filename}</Typography>
        <Typography variant="caption">
          Type: {source.file_type?.toUpperCase() || 'Unknown'}
        </Typography>
        <Typography variant="caption">
          Size: {formatFileSize(source.file_size || 0)}
        </Typography>
        <Typography variant="caption">
          Created: {formatDate(source.created_at)}
        </Typography>
        {source.llm_model && (
          <Typography variant="caption">
            Extraction Model: {source.llm_model}
          </Typography>
        )}
        {source.extraction_mode && (
          <Typography variant="caption">
            Extraction Mode: {source.extraction_mode.toUpperCase()}
          </Typography>
        )}
        {source.embedding_model && (
          <Typography variant="caption">
            Embedding Model: {source.embedding_model} ({source.embedding_dimensions}d)
          </Typography>
        )}
      </Box>

      {opts && (
        <>
          <Divider sx={{ my: 0.75 }} />
          <Typography
            variant="caption"
            sx={{ fontWeight: 600, display: 'block', mb: 0.5 }}
          >
            Upload settings
          </Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
            <Typography variant="caption">
              Auto-extract: {opts.auto_analyze ? 'Yes' : 'No'}
            </Typography>
            <Typography variant="caption">Depth: {opts.extraction_depth}</Typography>
            <Typography variant="caption">
              Domain: {opts.forced_domain ?? 'Auto-detect'}
              {source.domain_version ? ` · v${source.domain_version}` : ''}
            </Typography>
            {source.domain_changed_since_extraction && (
              <Typography variant="caption" sx={{ color: 'warning.main' }}>
                ⚠ plugin changed since extraction
              </Typography>
            )}
            <Typography variant="caption">
              Normalization: {describeNormalization(opts.enable_normalization)}
            </Typography>
            <Typography variant="caption">
              Vision: {opts.enable_vision ? 'On' : 'Off'}
            </Typography>
            <Typography variant="caption">
              Content filtering: {opts.content_filtering ? 'On' : 'Off'}
            </Typography>
            <Typography variant="caption">
              Filtering mode: {opts.filtering_mode}
            </Typography>
          </Box>
        </>
      )}
    </Box>
  );
}
