// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box } from '@mui/material';
import { SURFACE_BORDER } from '../../../../../theme/cardStyles';
import type { PipelineStat } from './pipelineStats';

interface PipelineStatCardsProps {
  stats: PipelineStat[];
}

/**
 * The Pipeline Flow section's resting state: a strip of compact stat cards
 * (Loaded · Cleaned · Chunks · LLM calls · Search). Centered with a muted
 * value color so they read as a quiet summary, not headline numbers.
 */
export function PipelineStatCards({ stats }: PipelineStatCardsProps) {
  if (stats.length === 0) return null;
  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(92px, 1fr))',
        gap: 1.5,
      }}
    >
      {stats.map((s) => (
        <Box
          key={s.label}
          sx={{
            border: `1px solid ${SURFACE_BORDER}`,
            borderRadius: 1,
            px: 1.75,
            py: 1.25,
            textAlign: 'center',
          }}
        >
          <Box sx={{ fontFamily: 'ui-monospace, monospace', fontSize: '1.1rem', fontWeight: 600, lineHeight: 1, color: '#aebcc6' }}>
            {s.value}
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 0.75, mt: 0.75, fontSize: '0.6rem', color: 'text.secondary', letterSpacing: 0.4 }}>
            <Box sx={{ width: 7, height: 7, borderRadius: '50%', bgcolor: s.dotColor, flexShrink: 0 }} />
            {s.label}
          </Box>
        </Box>
      ))}
    </Box>
  );
}
