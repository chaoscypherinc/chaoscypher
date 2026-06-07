// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Tooltip, Typography } from '@mui/material';
import { PipelineSeverityColors } from '../../../../theme/colors';
import type { FunnelStage } from './PipelineFunnel';
import type { StageStatItem, StatTone } from './stageStats';

const STAGE_ORDER: FunnelStage[] = ['load', 'clean', 'chunk', 'extract', 'filter', 'commit'];

const toneColor = (tone?: StatTone): string =>
  tone === 'warn' ? PipelineSeverityColors.warn : tone === 'err' ? PipelineSeverityColors.err : '#e6e6ea';

interface StageStatsBoardProps {
  stats: Record<FunnelStage, StageStatItem[]>;
}

/**
 * Aligned stage stats row. Shares the funnel's 6-column grid so each column
 * sits directly beneath its pill. Empty stages show a quiet "✓ clean".
 */
export function StageStatsBoard({ stats }: StageStatsBoardProps) {
  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: 'repeat(6, minmax(0, 1fr))',
        mt: 1.75,
        pt: 1.5,
        borderTop: '1px dashed rgba(255,255,255,0.08)',
      }}
    >
      {STAGE_ORDER.map((stage, idx) => {
        const items = stats[stage];
        return (
          <Box
            key={stage}
            data-stage={stage}
            sx={{ px: 1, borderLeft: idx === 0 ? 'none' : '1px solid rgba(255,255,255,0.05)' }}
          >
            {items.length === 0 ? (
              <Tooltip title="No drops, merges, or warnings recorded at this stage." arrow>
                <Typography sx={{ fontSize: '0.58rem', color: '#6fbf73', opacity: 0.6, cursor: 'help', display: 'inline-block' }}>
                  ✓ clean
                </Typography>
              </Tooltip>
            ) : (
              items.map((it) => {
                const row = (
                  <Box
                    sx={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      gap: 1,
                      py: 0.25,
                      cursor: it.description ? 'help' : 'default',
                    }}
                  >
                    <Typography sx={{ fontSize: '0.58rem', color: '#9aa6aa', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {it.label}
                    </Typography>
                    <Typography sx={{ fontSize: '0.6rem', fontFamily: 'ui-monospace, monospace', color: toneColor(it.tone) }}>
                      {it.value}
                    </Typography>
                  </Box>
                );
                return it.description ? (
                  <Tooltip key={it.label} title={it.description} arrow placement="top">
                    {row}
                  </Tooltip>
                ) : (
                  <Box key={it.label}>{row}</Box>
                );
              })
            )}
          </Box>
        );
      })}
    </Box>
  );
}
