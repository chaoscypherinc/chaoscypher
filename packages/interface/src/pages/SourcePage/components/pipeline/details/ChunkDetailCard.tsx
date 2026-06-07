// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Detail panel for the selected chunk/group in the per-chunk extraction hero.
 *
 * Status header + View/Rerun actions, an optional error line, and a row of
 * stat tiles (entities, relationships, tokens, duration). No raw text dump —
 * "View chunk" jumps to the chunk's text in the list below.
 */

import { Box, Button, Skeleton, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import type { ExtractionTask } from '../../../../../types';
import { ChunkAccent } from '../../../../../theme/colors';
import { SURFACE_BG, SURFACE_BORDER } from '../../../../../theme/cardStyles';
import { Overlays } from '../../../../../theme/overlays';

export interface ChunkDetailCardProps {
  task: ExtractionTask | null;
  loading: boolean;
  onRerun: (taskId: string) => void;
  /**
   * Fires with the whole task so the caller can deep-link by a real member
   * chunk id (``small_chunk_ids``) rather than the task's group ordinal —
   * tasks are per *group*, and the group ordinal does not line up with any
   * single chunk's ``chunk_index`` on the Chunks tab.
   */
  onViewChunk: (task: ExtractionTask) => void;
  isRerunning?: boolean;
}

const STATUS_COLOR: Record<string, string> = {
  failed: '#ef5350',
  completed: '#7fcc84',
  running: '#7eb3d4',
  pending: '#888',
  queued: '#888',
};

interface StatProps {
  value: React.ReactNode;
  label: string;
  color?: string;
}

/** One stat tile inside the detail panel's metric row. */
function Stat({ value, label, color }: StatProps) {
  return (
    <Box
      sx={{
        flex: 1,
        minWidth: 120,
        borderRadius: 1.5,
        p: 1.25,
        bgcolor: (theme) =>
          theme.palette.mode === 'dark' ? Overlays.subtle.dark : Overlays.subtle.light,
      }}
    >
      <Typography sx={{ fontSize: '1.1rem', fontWeight: 700, color: color ?? 'text.primary' }}>
        {value}
      </Typography>
      <Typography
        sx={{
          fontSize: '0.64rem',
          color: 'text.secondary',
          textTransform: 'uppercase',
          letterSpacing: 0.6,
          mt: 0.25,
        }}
      >
        {label}
      </Typography>
    </Box>
  );
}

export function ChunkDetailCard({
  task,
  loading,
  onRerun,
  onViewChunk,
  isRerunning,
}: ChunkDetailCardProps) {
  if (loading) {
    return (
      <Box
        data-testid="chunk-detail-skeleton"
        sx={{
          mt: 2,
          p: 2,
          bgcolor: 'background.paper',
          border: '1px solid',
          borderColor: (theme) =>
            theme.palette.mode === 'dark' ? Overlays.border.dark : Overlays.border.light,
          borderLeft: `3px solid ${ChunkAccent.ring}`,
          borderRadius: 2,
        }}
      >
        <Skeleton variant="text" width="30%" sx={{ mb: 1 }} />
        <Box sx={{ display: 'flex', gap: 1.25 }}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} variant="rounded" height={52} sx={{ flex: 1 }} />
          ))}
        </Box>
      </Box>
    );
  }
  if (!task) return null;

  const failed = task.status === 'failed';
  const statusColor = STATUS_COLOR[task.status] ?? '#888';
  const attempts = (task.retry_count ?? 0) + 1;
  const durationS = task.llm_duration_ms != null ? (task.llm_duration_ms / 1000).toFixed(1) : null;

  return (
    <Box
      sx={{
        mt: 2,
        p: 2,
        background: SURFACE_BG,
        border: '1px solid',
        borderColor: SURFACE_BORDER,
        borderLeft: `3px solid ${failed ? '#ef5350' : ChunkAccent.ring}`,
        borderRadius: 2,
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: '1rem', fontWeight: 700 }}>
          Chunk {task.chunk_index}
          <Box component="span" sx={{ color: statusColor, fontWeight: 600 }}>
            {' '}
            · {task.status}
          </Box>
          {attempts > 1 && (
            <Box component="span" sx={{ color: 'text.secondary', fontWeight: 400, fontSize: '0.82rem' }}>
              {' '}
              · {attempts} attempts
            </Box>
          )}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Button
          size="small"
          variant="outlined"
          startIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />}
          onClick={() => onViewChunk(task)}
          sx={{ fontSize: '0.72rem', textTransform: 'none', borderColor: ChunkAccent.edge, color: ChunkAccent.ring }}
        >
          View chunk
        </Button>
        <Button
          size="small"
          variant="outlined"
          startIcon={<RefreshIcon sx={{ fontSize: 14 }} />}
          onClick={() => onRerun(task.id)}
          disabled={isRerunning}
          sx={{ fontSize: '0.72rem', textTransform: 'none' }}
        >
          {isRerunning ? 'rerunning…' : 'Rerun chunk'}
        </Button>
      </Box>

      {task.error_message && (
        <Typography sx={{ color: '#ef5350', fontSize: '0.8rem', mb: 1.5 }}>
          Error: {task.error_message}
        </Typography>
      )}

      <Box sx={{ display: 'flex', gap: 1.25, flexWrap: 'wrap' }}>
        <Stat value={task.entity_count} label="Entities" color={failed ? 'text.disabled' : '#7fcc84'} />
        <Stat value={task.relationship_count} label="Relationships" />
        <Stat
          value={`${(task.input_tokens ?? 0).toLocaleString()} / ${(task.output_tokens ?? 0).toLocaleString()}`}
          label="Tokens in / out"
        />
        <Stat value={durationS != null ? `${durationS}s` : '—'} label="Duration" />
      </Box>
    </Box>
  );
}
