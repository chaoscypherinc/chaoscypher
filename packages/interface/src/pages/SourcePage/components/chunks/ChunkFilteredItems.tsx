// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
// ChunkFilteredItems.tsx
import { Box, Typography } from '@mui/material';
import type { FilteringLog } from '../../../../types';
import { FilterStageItems } from '../FilterStageItems';

export interface ChunkFilteredItemsProps {
  filteringLog: FilteringLog | null;
}

function countByType(log: FilteringLog): { entities: number; relationships: number } {
  let entities = 0;
  let relationships = 0;
  for (const stage of log.stages) {
    for (const item of stage.items) {
      if (item.item_type === 'entity') entities += 1;
      else if (item.item_type === 'relationship') relationships += 1;
    }
  }
  return { entities, relationships };
}

export function ChunkFilteredItems({ filteringLog }: ChunkFilteredItemsProps) {
  if (!filteringLog || filteringLog.stages.length === 0) return null;
  const { entities, relationships } = countByType(filteringLog);
  return (
    <Box
      sx={{
        mt: 1.5,
        bgcolor: 'rgba(244,67,54,0.04)',
        border: '1px solid rgba(244,67,54,0.3)',
        borderRadius: 0.5,
        p: 1.5,
      }}
    >
      <Typography sx={{ color: '#ef5350', fontSize: '0.65rem', letterSpacing: 0.5, mb: 1 }}>
        FILTERED OUT ({entities} entities + {relationships} relationships dropped from this chunk)
      </Typography>
      <FilterStageItems filteringLog={filteringLog} />
    </Box>
  );
}
