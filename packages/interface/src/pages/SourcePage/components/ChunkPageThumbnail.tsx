// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Right-side thumbnail for a single chunk row.
 *
 * Four render branches, ordered by priority:
 *   1. ``imageUrl`` is set → real thumbnail (lazy-loaded, click to
 *      expand via the ``onExpand`` callback).
 *   2. ``pageIsRenderFailed`` → red "Render failed" placeholder.
 *      (Reserved; always false after migration 0034 dropped the
 *      legacy loader_pdf_failed_pages column.)
 *   3. ``pageIsVisionFailed`` → yellow "Vision failed" placeholder.
 *      (Reserved; always false after migration 0034 dropped the
 *      legacy vision_failed_pages column.)
 *   4. ``expectedImage`` (Tier-1 fallback) → yellow "Render failed"
 *      placeholder for chunks with a page_number but where we have
 *      no row-level classification. Covers future failure modes the
 *      backend hasn't classified yet.
 *   5. Otherwise → render nothing, so the chunk text uses the full
 *      row width.
 */

import { Box, Tooltip, Typography } from '@mui/material';
import BrokenImageIcon from '@mui/icons-material/BrokenImage';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import ErrorOutlinedIcon from '@mui/icons-material/ErrorOutlined';
import { Overlays } from '../../../theme/overlays';

interface ChunkPageThumbnailProps {
  /** Resolved image URL when the page rendered and survived vision (or had no vision). */
  imageUrl: string | null;
  /** 1-indexed page number for this chunk, or undefined for non-paginated sources. */
  pageNumber: number | undefined;
  /**
   * Whether this row "expected" an image — i.e. the source has page
   * images at all and this chunk has a page number. Drives the Tier-1
   * legacy fallback when we don't have a row-level classification.
   */
  expectedImage: boolean;
  /** True if this page failed vision processing (always false after migration 0034). */
  pageIsVisionFailed: boolean;
  /** True if this page failed PDF rendering (always false after migration 0034). */
  pageIsRenderFailed: boolean;
  /** Expand callback fired when the real thumbnail is clicked. */
  onExpand: (url: string) => void;
}

/** Shared box layout for placeholder thumbnails. */
const PLACEHOLDER_BOX_SX = {
  width: 120,
  height: 150,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 0.5,
  borderRadius: 1,
  border: '1px dashed',
  cursor: 'help',
} as const;

export function ChunkPageThumbnail({
  imageUrl,
  pageNumber,
  expectedImage,
  pageIsVisionFailed,
  pageIsRenderFailed,
  onExpand,
}: ChunkPageThumbnailProps) {
  // 1. Real thumbnail — image exists.
  if (imageUrl) {
    return (
      <Tooltip
        title={pageNumber ? `Page ${pageNumber} — click to expand` : 'Click to expand'}
        arrow
      >
        <Box
          component="img"
          src={imageUrl}
          alt={pageNumber ? `Page ${pageNumber}` : 'Page thumbnail'}
          loading="lazy"
          onClick={() => onExpand(imageUrl)}
          sx={{
            width: 120,
            height: 150,
            objectFit: 'cover',
            objectPosition: 'top',
            borderRadius: 1,
            border: '1px solid',
            borderColor: 'divider',
            cursor: 'pointer',
            transition: 'transform 0.15s, border-color 0.15s',
            '&:hover': {
              transform: 'scale(1.02)',
              borderColor: 'primary.main',
            },
          }}
        />
      </Tooltip>
    );
  }

  // 2. Render failure — pypdfium2 raised; no PNG was written.
  if (pageIsRenderFailed) {
    return (
      <Tooltip
        title="PDF rendering failed on this page — image unavailable."
        arrow
      >
        <Box
          sx={{
            ...PLACEHOLDER_BOX_SX,
            borderColor: 'error.main',
            color: 'error.main',
            bgcolor: (theme) =>
              theme.palette.mode === 'dark'
                ? Overlays.subtle.dark
                : Overlays.subtle.light,
          }}
        >
          <ErrorOutlinedIcon sx={{ fontSize: 28 }} />
          <Typography variant="caption" sx={{ fontSize: '0.65rem', fontWeight: 600 }}>
            Render failed
          </Typography>
          {pageNumber !== undefined && (
            <Typography variant="caption" sx={{ fontSize: '0.6rem' }}>
              p.{pageNumber}
            </Typography>
          )}
        </Box>
      </Tooltip>
    );
  }

  // 3. Vision LLM failure — image rendered but description came back None.
  if (pageIsVisionFailed) {
    return (
      <Tooltip
        title="Vision processing failed on this page — visual context missing."
        arrow
      >
        <Box
          sx={{
            ...PLACEHOLDER_BOX_SX,
            borderColor: 'warning.main',
            color: 'warning.main',
            bgcolor: (theme) =>
              theme.palette.mode === 'dark'
                ? Overlays.subtle.dark
                : Overlays.subtle.light,
          }}
        >
          <WarningAmberIcon sx={{ fontSize: 28 }} />
          <Typography variant="caption" sx={{ fontSize: '0.65rem', fontWeight: 600 }}>
            Vision failed
          </Typography>
          {pageNumber !== undefined && (
            <Typography variant="caption" sx={{ fontSize: '0.6rem' }}>
              p.{pageNumber}
            </Typography>
          )}
        </Box>
      </Tooltip>
    );
  }

  // 4. Tier-1 fallback — image missing, no row-level classification.
  if (expectedImage) {
    return (
      <Tooltip
        title={
          pageNumber
            ? `Page ${pageNumber} thumbnail unavailable — rendering or vision processing failed on this page.`
            : 'Page thumbnail unavailable — rendering or vision processing failed on this page.'
        }
        arrow
      >
        <Box
          sx={{
            ...PLACEHOLDER_BOX_SX,
            borderColor: 'warning.main',
            color: 'warning.main',
            bgcolor: (theme) =>
              theme.palette.mode === 'dark'
                ? Overlays.subtle.dark
                : Overlays.subtle.light,
          }}
        >
          <BrokenImageIcon sx={{ fontSize: 28 }} />
          <Typography variant="caption" sx={{ fontSize: '0.65rem', fontWeight: 600 }}>
            Render failed
          </Typography>
          {pageNumber !== undefined && (
            <Typography variant="caption" sx={{ fontSize: '0.6rem' }}>
              p.{pageNumber}
            </Typography>
          )}
        </Box>
      </Tooltip>
    );
  }

  // 5. No image expected — render nothing.
  return null;
}
