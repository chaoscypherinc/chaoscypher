// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Per-chunk extraction navigator — the hero of the Chunks tab.
 *
 * Each chunk/group is a stat-card tile (chunk number, status badge, entity
 * count as the headline metric); clicking one selects it and shows its detail
 * card below, while the band's "View chunk" jumps to its text in the list.
 *
 * Scaling: the tiles are a bounded WINDOW (`WINDOW_SIZE`). When a source has
 * more groups than fit, a thin whole-source status rail appears above the
 * tiles (every group, current window bright / rest dimmed, failures always
 * coloured, click to jump) plus a pager. Small sources skip the rail + pager
 * and just render every tile.
 */

import { useState } from 'react';
import { Box, Button, Typography } from '@mui/material';
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import type { ExtractionChartTask } from '../../../../../types';
import { ChunkAccent } from '../../../../../theme/colors';
import { SURFACE_BG, SURFACE_BORDER, surfaceHoverSx } from '../../../../../theme/cardStyles';
import { ChunkStatusRail } from './ChunkStatusRail';
import { chunkStatusKind, ChunkStatusColor, ChunkStatusLabel } from './chunkStatus';

/** Tiles shown per window before the rail + pager kick in. */
const WINDOW_SIZE = 24;

export interface ChunkGridProps {
  tasks: ExtractionChartTask[];
  selectedChunkId: string | null;
  onSelectChunk: (id: string | null) => void;
}

interface ChunkTileProps {
  task: ExtractionChartTask;
  selected: boolean;
  onSelect: (id: string | null) => void;
}

/** One chunk/group as a stat card. */
function ChunkTile({ task, selected, onSelect }: ChunkTileProps) {
  const kind = chunkStatusKind(task);
  const accent = ChunkStatusColor[kind];
  const failed = kind === 'failed';
  const metricLabel = failed ? 'failed' : kind === 'pending' ? 'pending' : 'entities';

  return (
    <Box
      component="button"
      type="button"
      data-testid={`chunk-cell-${task.id}`}
      data-chunk-index={task.chunk_index}
      data-selected={selected ? 'true' : 'false'}
      onClick={() => onSelect(selected ? null : task.id)}
      sx={{
        all: 'unset',
        display: 'block',
        cursor: 'pointer',
        boxSizing: 'border-box',
        p: 1.25,
        borderRadius: 2,
        border: '1px solid',
        borderColor: SURFACE_BORDER,
        borderTop: `3px solid ${accent.main}`,
        // Shared dark surface (failed tiles keep their red wash) so the hero
        // tiles match the obsidian surfaces across the rest of the tabs.
        bgcolor: failed ? accent.bg : SURFACE_BG,
        transition: 'transform 0.12s, border-color 0.12s',
        ...(selected && { outline: `2px solid ${ChunkAccent.ring}`, outlineOffset: 1 }),
        '&:hover': {
          transform: 'translateY(-1px)',
          borderColor: surfaceHoverSx.borderColor,
        },
        '&:focus-visible': { outline: `2px solid ${ChunkAccent.ring}`, outlineOffset: 1 },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 0.75 }}>
        <Typography sx={{ fontSize: '0.78rem', color: 'text.secondary', fontWeight: 600 }}>
          #{task.chunk_index}
        </Typography>
        <Box
          component="span"
          sx={{
            fontSize: '0.6rem',
            fontWeight: 700,
            letterSpacing: 0.3,
            px: 0.75,
            py: '2px',
            borderRadius: 5,
            color: accent.main,
            bgcolor: accent.bg,
          }}
        >
          {ChunkStatusLabel[kind]}
        </Box>
      </Box>
      <Typography
        sx={{
          fontSize: '1.6rem',
          fontWeight: 700,
          lineHeight: 1.05,
          color: failed ? 'text.disabled' : 'text.primary',
        }}
      >
        {failed ? '—' : task.entity_count}
      </Typography>
      <Typography sx={{ fontSize: '0.66rem', color: 'text.secondary', mt: 0.25 }}>
        {metricLabel}
      </Typography>
    </Box>
  );
}

export function ChunkGrid({ tasks, selectedChunkId, onSelectChunk }: ChunkGridProps) {
  const [page, setPage] = useState(0);

  if (tasks.length === 0) {
    return (
      <Typography
        sx={{ color: 'text.secondary', fontSize: '0.78rem', fontStyle: 'italic', textAlign: 'center', py: 1.5 }}
      >
        No chunk tasks yet — extraction hasn't started.
      </Typography>
    );
  }

  const sorted = [...tasks].sort((a, b) => a.chunk_index - b.chunk_index);
  const windowed = sorted.length > WINDOW_SIZE;
  const lastPage = Math.max(0, Math.ceil(sorted.length / WINDOW_SIZE) - 1);
  // Derive the effective page rather than snapping via an effect: if `tasks`
  // shrinks (e.g. a refetch) the stored page is clamped here, no cascade.
  const safePage = Math.min(page, lastPage);
  const start = windowed ? safePage * WINDOW_SIZE : 0;
  const end = windowed ? Math.min(start + WINDOW_SIZE, sorted.length) : sorted.length;
  const visible = sorted.slice(start, end);

  const handleJump = (task: ExtractionChartTask, index: number) => {
    setPage(Math.floor(index / WINDOW_SIZE));
    onSelectChunk(task.id);
  };

  return (
    <Box>
      {windowed && (
        <Box sx={{ mb: 1.5 }}>
          <Box
            sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.75, gap: 1 }}
          >
            <Typography
              sx={{ fontSize: '0.66rem', color: 'text.secondary', textTransform: 'uppercase', letterSpacing: 0.8 }}
            >
              whole source · viewing {start + 1}–{end} of {sorted.length}
            </Typography>
            <Typography sx={{ fontSize: '0.62rem', color: 'text.disabled' }}>
              <Box component="span" sx={{ color: ChunkStatusColor.ok.main }}>
                ▮
              </Box>{' '}
              ok{' '}
              <Box component="span" sx={{ color: ChunkStatusColor.retried.main }}>
                ▮
              </Box>{' '}
              retried{' '}
              <Box component="span" sx={{ color: ChunkStatusColor.failed.main }}>
                ▮
              </Box>{' '}
              failed · dim = off page
            </Typography>
          </Box>
          <ChunkStatusRail
            tasks={sorted}
            windowStart={start}
            windowEnd={end}
            selectedChunkId={selectedChunkId}
            onJump={handleJump}
          />
        </Box>
      )}

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(118px, 1fr))',
          gap: 1.25,
        }}
      >
        {visible.map((t) => (
          <ChunkTile
            key={t.id}
            task={t}
            selected={selectedChunkId === t.id}
            onSelect={onSelectChunk}
          />
        ))}
      </Box>

      {windowed && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 2,
            mt: 2,
            color: 'text.secondary',
            fontSize: '0.78rem',
          }}
        >
          <Button
            size="small"
            variant="outlined"
            disabled={safePage === 0}
            onClick={() => setPage(safePage - 1)}
            startIcon={<ChevronLeftIcon sx={{ fontSize: 16 }} />}
            sx={{ textTransform: 'none', fontSize: '0.72rem' }}
          >
            Prev
          </Button>
          <Box component="span">
            Page <b>{safePage + 1}</b> / {lastPage + 1} · chunks <b>{start + 1}–{end}</b> of{' '}
            {sorted.length}
          </Box>
          <Button
            size="small"
            variant="outlined"
            disabled={safePage === lastPage}
            onClick={() => setPage(safePage + 1)}
            endIcon={<ChevronRightIcon sx={{ fontSize: 16 }} />}
            sx={{ textTransform: 'none', fontSize: '0.72rem' }}
          >
            Next
          </Button>
        </Box>
      )}
    </Box>
  );
}
