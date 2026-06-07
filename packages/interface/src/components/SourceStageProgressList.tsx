// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SourceStageProgressList — iterates a source's stage_progress dict and
 * renders one inline indicator per active stage, each with a tooltip
 * carrying the full stage detail.
 *
 * Single source of truth for stage-name → display-label translation.
 * Adding a new stage = adding one entry to STAGE_DISPLAY.
 */

import { useMemo } from 'react';
import { Box, Tooltip, Typography } from '@mui/material';
import type { components } from '../types/generated/api';
import { LLMStageInline, LLMStageTooltip } from './LLMStageProgress';


type StageProgressRecord = components['schemas']['StageProgressRecord'];


const STAGE_DISPLAY: Record<string, { label: string; itemNoun: string }> = {
  vision: { label: 'Vision processing', itemNoun: 'pages' },
  embedding: { label: 'Embedding', itemNoun: 'chunks' },
  mcp_extraction: { label: 'MCP Entity Extraction', itemNoun: 'chunks' },
};

const STALE_THRESHOLD_MS = 10 * 60 * 1000;  // 10 minutes


interface Props {
  stageProgress: Record<string, StageProgressRecord>;
  onStaleClick?: (stageName: string) => void;
}

export function SourceStageProgressList({ stageProgress, onStaleClick }: Props) {
  // Snapshot the current timestamp for staleness comparisons. Updated each
  // time stageProgress changes so we see a fresh "now" with each data tick.
  // stageProgress is an intentional dep even though the callback doesn't
  // reference it directly — it drives the refresh cadence.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- stageProgress intentionally triggers refresh
  const now = useMemo(() => Date.now(), [stageProgress]);

  const active = Object.entries(stageProgress).filter(
    ([, r]) => r.completed_at == null && r.total > 0
  );
  if (active.length === 0) return null;

  return (
    <>
      {active.map(([stageName, r]) => {
        const display = STAGE_DISPLAY[stageName] ?? { label: stageName, itemNoun: 'items' };
        const isStale =
          (now - new Date(r.last_activity).getTime()) > STALE_THRESHOLD_MS;
        const extras = r.extras ?? null;

        return (
          <Tooltip
            key={stageName}
            arrow placement="top"
            title={
              <LLMStageTooltip
                label={display.label}
                processed={r.processed}
                total={r.total}
                itemNoun={display.itemNoun}
                avgMs={r.avg_ms}
              >
                {extras?.entities_preview != null && (
                  <Typography variant="caption" sx={{ display: 'block' }}>
                    Entities found: {String(extras.entities_preview)}
                  </Typography>
                )}
                {extras?.relationships_preview != null && (
                  <Typography variant="caption" sx={{ display: 'block' }}>
                    Relationships found: {String(extras.relationships_preview)}
                  </Typography>
                )}
                {isStale && (
                  <>
                    <Box sx={{ borderTop: '1px solid', borderColor: 'divider', my: 0.5 }} />
                    <Typography variant="caption" sx={{ display: 'block', color: 'warning.main' }}>
                      No activity for 10+ minutes.
                    </Typography>
                  </>
                )}
              </LLMStageTooltip>
            }
          >
            <Box
              sx={{
                cursor: isStale && onStaleClick ? 'pointer' : 'help',
                display: 'inline-flex',
              }}
              onClick={isStale && onStaleClick ? () => onStaleClick(stageName) : undefined}
            >
              <LLMStageInline
                processed={r.processed}
                total={r.total}
                itemNoun={display.itemNoun}
                avgMs={r.avg_ms}
              />
            </Box>
          </Tooltip>
        );
      })}
    </>
  );
}
