// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Chip } from '@mui/material';
import type { FilteringLog } from '../../../types';
import { FilterStageItems } from './FilterStageItems';

// ---------------------------------------------------------------------------
// FilteringLogPanel
// ---------------------------------------------------------------------------

function isEntityStage(stage: string): boolean {
  return stage.includes('entity') || stage === 'type_rescue' || stage === 'implausible_entity_filter';
}

interface FilteringLogPanelProps {
  filteringLog: FilteringLog;
  compact?: boolean;
}

export function FilteringLogPanel({ filteringLog, compact = false }: FilteringLogPanelProps) {
  if (!filteringLog.stages || filteringLog.stages.length === 0) {
    return null;
  }

  const entityStages = filteringLog.stages.filter((s) => isEntityStage(s.stage));
  const relStages = filteringLog.stages.filter((s) => !isEntityStage(s.stage));
  const totalEntityRemoved = entityStages.reduce((sum, s) => sum + s.removed_count, 0);
  const totalRelRemoved = relStages.reduce((sum, s) => sum + s.removed_count, 0);

  return (
    <Box>
      {/* Summary chips */}
      <Box sx={{ display: 'flex', gap: 1, mb: compact ? 1 : 2, flexWrap: 'wrap' }}>
        {!compact && (
          <Chip
            label={`${filteringLog.total_removed} total removed`}
            size="small"
            variant="outlined"
            sx={{ fontSize: '0.75rem', height: 24, fontWeight: 600 }}
          />
        )}
        {totalEntityRemoved > 0 && (
          <Chip
            label={compact ? `${totalEntityRemoved} entities filtered` : `${totalEntityRemoved} entities`}
            size="small"
            color="info"
            variant="outlined"
            sx={{ fontSize: '0.75rem', height: compact ? 22 : 24 }}
          />
        )}
        {totalRelRemoved > 0 && (
          <Chip
            label={compact ? `${totalRelRemoved} relationships filtered` : `${totalRelRemoved} relationships`}
            size="small"
            color="error"
            variant="outlined"
            sx={{ fontSize: '0.75rem', height: compact ? 22 : 24 }}
          />
        )}
      </Box>
      <FilterStageItems filteringLog={filteringLog} />
    </Box>
  );
}
