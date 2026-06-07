// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * VisionPagesGrid — per-source vision-pages status as a cell grid.
 *
 * Replacement for the legacy ``VisionPagesPanel`` row-stack layout. Renders
 * a grid of compact status cells (one per vision page) matching the visual
 * language of ``ChunkGrid``. Clicking a cell expands an inline detail panel
 * below the grid with status, error message, region index, and a per-page
 * retry button (only while the source is still ``vision_pending``). The
 * detail panel includes an inline page thumbnail; clicking the thumbnail
 * opens a full-resolution MUI ``Dialog`` lightbox.
 *
 * Polling cadence (5s while ``vision_pending``) and the "Retry N failed"
 * batch button mirror the legacy panel — operators still see pages flip
 * from pending → succeeded/failed without a manual refresh, and the retry
 * affordances are hidden once the pipeline has advanced past
 * ``vision_pending`` (retrying past finalize has no downstream effect, but
 * the read-only audit view remains).
 *
 * Returns ``null`` for sources without a ``vision_job`` (text-only).
 */

import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  IconButton,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import RefreshIcon from '@mui/icons-material/Refresh';
import {
  useRetryFailedVisionPages,
  useRetryVisionPage,
  useVisionPages,
  type VisionPage,
} from '../../../../../services/api/useVisionPages';
import { useSourceImages } from '../../../../../services/api/useSourceImages';

export interface VisionPagesGridProps {
  sourceId: string;
  sourceStatus: string;
}

function cellStyle(status: VisionPage['status']) {
  if (status === 'failed') {
    return { bg: 'rgba(244,67,54,0.20)', color: '#ef5350', border: '1px solid rgba(244,67,54,0.5)' };
  }
  if (status === 'succeeded') {
    return { bg: 'rgba(91,154,95,0.15)', color: '#7fcc84', border: 'none' };
  }
  if (status === 'truncated') {
    return { bg: 'rgba(255,167,38,0.18)', color: '#ffa726', border: 'none' };
  }
  return { bg: 'rgba(255,255,255,0.04)', color: '#888', border: 'none' };
}

export function VisionPagesGrid({ sourceId, sourceStatus }: VisionPagesGridProps) {
  const isPreFinalize = sourceStatus === 'vision_pending';
  const refetchInterval: number | false = isPreFinalize ? 5000 : false;

  const { data, isLoading, error } = useVisionPages(sourceId, { refetchInterval });
  const retryOne = useRetryVisionPage(sourceId);
  const retryAll = useRetryFailedVisionPages(sourceId);
  const { data: images } = useSourceImages(sourceId, true);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);

  if (isLoading) {
    return <Box sx={{ p: 1.5, textAlign: 'center' }}><CircularProgress size={20} /></Box>;
  }
  if (error) {
    return <Alert severity="error">Failed to load vision pages: {error.message}</Alert>;
  }
  if (!data || data.job === null) return null;

  const failedPages = data.pages.filter((p) => p.status === 'failed');
  const canRetryBatch = isPreFinalize && failedPages.length > 0;
  const selected = selectedId ? data.pages.find((p) => p.id === selectedId) ?? null : null;
  const selectedImageUrl = selected
    ? images?.find((img) => img.filename === `page_${selected.page_number}.png`)?.url ?? null
    : null;

  return (
    <Box
      sx={{
        bgcolor: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 0.5,
        p: 1.5,
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
        <Typography sx={{ fontSize: '0.7rem', color: '#aaa', flex: 1 }}>
          👁 VISION PROCESSING · {data.job.completed} / {data.job.total_pages} pages
        </Typography>
        {canRetryBatch && (
          <Button
            startIcon={<RefreshIcon sx={{ fontSize: 12 }} />}
            size="small"
            variant="outlined"
            onClick={() => retryAll.mutate()}
            disabled={retryAll.isPending}
            sx={{ fontSize: '0.65rem' }}
          >
            ↻ retry {failedPages.length} failed
          </Button>
        )}
      </Box>
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(72px, 1fr))',
          gap: 0.5,
          fontFamily: 'ui-monospace, monospace',
          fontSize: '0.6rem',
        }}
      >
        {data.pages.map((p) => {
          const style = cellStyle(p.status);
          const isSelected = selectedId === p.id;
          return (
            <Box
              key={p.id}
              data-testid={`vision-cell-${p.id}`}
              onClick={() => setSelectedId(isSelected ? null : p.id)}
              sx={{
                background: style.bg,
                color: style.color,
                border: style.border,
                py: 0.5,
                px: 0.5,
                borderRadius: 0.25,
                textAlign: 'center',
                cursor: 'pointer',
                outline: isSelected ? '2px solid #fff' : 'none',
                '&:hover': { filter: 'brightness(1.15)' },
              }}
            >
              p{p.page_number} {p.status === 'succeeded' ? '✓' : p.status === 'failed' ? '✗' : '…'}
            </Box>
          );
        })}
      </Box>

      {selected && (
        <Box
          data-testid="vision-detail-panel"
          sx={{
            mt: 1.5,
            p: 1.5,
            bgcolor: selected.status === 'failed' ? 'rgba(244,67,54,0.06)' : 'rgba(91,154,95,0.04)',
            border: '1px solid',
            borderColor: selected.status === 'failed' ? 'rgba(244,67,54,0.4)' : 'rgba(91,154,95,0.3)',
            borderRadius: 0.5,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
            <Typography
              sx={{
                color: selected.status === 'failed' ? '#ef5350' : '#7fcc84',
                fontWeight: 600,
                fontSize: '0.78rem',
                flex: 1,
              }}
            >
              ▾ Page {selected.page_number} · {selected.status}
            </Typography>
            {isPreFinalize && selected.status === 'failed' && (
              <Button
                size="small"
                variant="outlined"
                startIcon={<RefreshIcon sx={{ fontSize: 12 }} />}
                onClick={() =>
                  retryOne.mutate({
                    pageNumber: selected.page_number,
                    regionIndex: selected.region_index,
                  })
                }
                disabled={retryOne.isPending}
                sx={{ fontSize: '0.65rem' }}
              >
                ↻ retry page
              </Button>
            )}
          </Box>
          <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>
            <Box sx={{ flex: '0 0 180px' }}>
              <Typography sx={{ fontSize: '0.6rem', color: '#aaa', mb: 0.75 }}>
                📄 PAGE IMAGE {selectedImageUrl && '· click to enlarge'}
              </Typography>
              {selectedImageUrl ? (
                <Box
                  component="img"
                  src={selectedImageUrl}
                  alt={`page ${selected.page_number}`}
                  onClick={() => setLightboxUrl(selectedImageUrl)}
                  sx={{
                    width: '100%',
                    borderRadius: 0.5,
                    cursor: 'zoom-in',
                    border: '1px solid rgba(255,255,255,0.12)',
                  }}
                />
              ) : (
                <Box
                  sx={{
                    aspectRatio: '0.77',
                    bgcolor: 'rgba(255,255,255,0.04)',
                    border: '1px dashed rgba(255,255,255,0.12)',
                    borderRadius: 0.5,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#666',
                    fontSize: '0.65rem',
                    p: 1,
                    textAlign: 'center',
                  }}
                >
                  Page image not available
                </Box>
              )}
            </Box>
            <Box
              sx={{ flex: 1, fontFamily: 'ui-monospace, monospace', fontSize: '0.65rem', color: '#bbb', lineHeight: 1.6 }}
            >
              {selected.error_message && (
                <Box sx={{ color: '#ef5350' }}>Error: {selected.error_message}</Box>
              )}
              <Box>
                Region: {selected.region_index}
              </Box>
            </Box>
          </Box>
        </Box>
      )}

      <Dialog open={!!lightboxUrl} onClose={() => setLightboxUrl(null)} maxWidth="lg" fullWidth>
        <Box sx={{ position: 'relative', bgcolor: '#000' }}>
          <IconButton
            aria-label="close"
            onClick={() => setLightboxUrl(null)}
            sx={{ position: 'absolute', top: 8, right: 8, color: '#fff', bgcolor: 'rgba(0,0,0,0.5)' }}
          >
            <CloseIcon />
          </IconButton>
          {lightboxUrl && (
            <Box component="img" src={lightboxUrl} alt="page lightbox" sx={{ width: '100%', display: 'block' }} />
          )}
        </Box>
      </Dialog>
    </Box>
  );
}
