// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Whole-source status rail for the per-chunk extraction hero.
 *
 * One thin segment per chunk/group across the ENTIRE source, coloured by
 * status. The tile grid only shows a bounded window at a time, so this rail
 * is the at-a-glance overview: segments inside the current window are bright,
 * the rest are dimmed (but failures/retries keep their colour so a problem
 * anywhere in the doc is always visible). Clicking a segment jumps the window
 * to it and selects it.
 */

import { Box, Tooltip } from '@mui/material';
import type { ExtractionChartTask } from '../../../../../types';
import { ChunkAccent } from '../../../../../theme/colors';
import { chunkStatusKind, ChunkStatusColor, ChunkStatusLabel } from './chunkStatus';

export interface ChunkStatusRailProps {
  /** All tasks for the source, already sorted by chunk_index. */
  tasks: ExtractionChartTask[];
  /** Inclusive start index of the visible window (into `tasks`). */
  windowStart: number;
  /** Exclusive end index of the visible window (into `tasks`). */
  windowEnd: number;
  selectedChunkId: string | null;
  /** Fires when a segment is clicked — caller jumps the window + selects. */
  onJump: (task: ExtractionChartTask, index: number) => void;
}

export function ChunkStatusRail({
  tasks,
  windowStart,
  windowEnd,
  selectedChunkId,
  onJump,
}: ChunkStatusRailProps) {
  return (
    <Box
      role="group"
      aria-label="Whole-source chunk status — click a segment to jump"
      data-testid="chunk-status-rail"
      sx={{ display: 'flex', gap: '1px', height: 12, alignItems: 'stretch' }}
    >
      {tasks.map((t, i) => {
        const kind = chunkStatusKind(t);
        const inWindow = i >= windowStart && i < windowEnd;
        const selected = selectedChunkId === t.id;
        const detail =
          kind === 'failed' ? '' : ` · ${t.entity_count} entities`;
        return (
          <Tooltip
            key={t.id}
            title={`Chunk ${t.chunk_index} — ${ChunkStatusLabel[kind]}${detail}`}
            arrow
            disableInteractive
          >
            <Box
              component="button"
              type="button"
              aria-label={`Chunk ${t.chunk_index}, ${ChunkStatusLabel[kind]} — jump`}
              onClick={() => onJump(t, i)}
              sx={{
                all: 'unset',
                flex: '1 1 0',
                minWidth: 2,
                height: '100%',
                cursor: 'pointer',
                borderRadius: '2px',
                bgcolor: ChunkStatusColor[kind].main,
                opacity: inWindow ? 1 : 0.28,
                outline: selected ? `2px solid ${ChunkAccent.ring}` : 'none',
                outlineOffset: 1,
                transition: 'opacity 0.15s, filter 0.15s',
                '&:hover': { opacity: 1, filter: 'brightness(1.2)' },
                '&:focus-visible': { outline: `2px solid ${ChunkAccent.ring}`, outlineOffset: 1 },
              }}
            />
          </Tooltip>
        );
      })}
    </Box>
  );
}
