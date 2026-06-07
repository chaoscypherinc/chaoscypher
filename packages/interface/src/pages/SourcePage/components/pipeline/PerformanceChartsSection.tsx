// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Performance charts layout rendered inside the Chunks tab's Chunk
 * Overview band (collapsible "Performance" sub-section).
 *
 * Three existing chart components reused verbatim — ContextUtilization,
 * ProcessingTime, EntityDensity. The change is layout + the
 * click-to-expand interaction:
 *
 *   Processing Time      |    Entity Density
 *   ────────────────────────────────────────────────
 *   Context Utilization (muted background side note)
 *
 * Clicking ProcessingTime / EntityDensity expands the chart inline
 * directly below the row at full height. Context utilization sits last
 * as the per-call token-budget summary, kept compact and secondary (quiet
 * label, no card frame, no expand affordance) but in full colour so it
 * reads crisply rather than washed out.
 *
 * Implementation intentionally keeps the chart components untouched —
 * we wrap them in click targets, not modify them.
 */

import { useState } from 'react';
import { Box, IconButton, Tooltip, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import OpenInFullIcon from '@mui/icons-material/OpenInFull';
import type { ExtractionChartTask, ExtractionTask, ExtractionTaskStats } from '../../../../types';
import { surfaceSx, surfaceHoverSx } from '../../../../theme/cardStyles';
import {
  ContextUtilizationChart,
  ProcessingTimeChart,
  EntityDensityChart,
} from '../charts';

type PerformanceChartKey = 'time' | 'density';

interface PerformanceChartsSectionProps {
  tasks: ExtractionTask[];
  chartTasks: ExtractionChartTask[];
  stats: ExtractionTaskStats | null;
}

interface ChartCardProps {
  title: string;
  /**
   * When set, the card shows an "Expand chart" affordance that fires
   * this callback. Omit for charts that already show full detail at
   * their default size (Context utilization).
   */
  onExpand?: () => void;
  /**
   * Plain-language explanation of what the chart measures. Rendered as a
   * hover tooltip on the title so the cryptic short labels are self-explaining.
   */
  info?: string;
  children: React.ReactNode;
}

function ChartCard({ title, onExpand, info, children }: ChartCardProps) {
  const titleEl = (
    <Typography
      variant="overline"
      sx={{ flex: 1, fontSize: '0.7rem', letterSpacing: 1, opacity: 0.7, cursor: info ? 'help' : 'default' }}
    >
      {title}
    </Typography>
  );
  return (
    <Box
      sx={{
        ...surfaceSx,
        borderRadius: 1,
        p: 1.5,
        position: 'relative',
        transition: 'border-color 0.15s',
        '&:hover': { borderColor: surfaceHoverSx.borderColor },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
        {info ? (
          <Tooltip title={info} arrow>
            {titleEl}
          </Tooltip>
        ) : (
          titleEl
        )}
        {onExpand && (
          <Tooltip title="Expand chart" arrow>
            <IconButton
              size="small"
              onClick={onExpand}
              aria-label={`Expand ${title}`}
              sx={{ opacity: 0.6, '&:hover': { opacity: 1 } }}
            >
              <OpenInFullIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
        )}
      </Box>
      {children}
    </Box>
  );
}

export function PerformanceChartsSection({
  tasks,
  chartTasks,
  stats,
}: PerformanceChartsSectionProps) {
  const [expanded, setExpanded] = useState<PerformanceChartKey | null>(null);

  // Auto-hide the whole section when extraction hasn't run — sections
  // appear only when they have data, so there's no empty-state
  // placeholder band.
  if (chartTasks.length === 0 && tasks.length === 0) {
    return null;
  }

  return (
    <Box sx={{ mb: 2 }}>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1 }}>
        <ChartCard
          title="⏱ Processing time / chunk"
          info="Wall-clock time the LLM took to extract each chunk group. Spikes usually mean a larger chunk, a retry, or a slow provider response."
          onExpand={() => setExpanded('time')}
        >
          <ProcessingTimeChart chartTasks={chartTasks} stats={stats} height={140} />
        </ChartCard>
        <ChartCard
          title="⊜ Entity density / chunk"
          info="Entities (and relationships) extracted per chunk group. Low density can mean sparse content or an aggressive filter; high density, an information-rich chunk."
          onExpand={() => setExpanded('density')}
        >
          <EntityDensityChart chartTasks={chartTasks} stats={stats} height={140} />
        </ChartCard>
      </Box>

      {expanded && (
        <Box
          data-testid={`performance-expanded-${expanded}`}
          sx={{
            mt: 1,
            p: 1.5,
            bgcolor: 'rgba(0, 229, 255, 0.04)',
            border: '1px solid rgba(0, 229, 255, 0.25)',
            borderRadius: 1,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
            <Typography variant="overline" sx={{ flex: 1, fontSize: '0.7rem', letterSpacing: 1, opacity: 0.85 }}>
              {expanded === 'time'
                ? '⏱ Processing time — expanded'
                : '⊜ Entity density — expanded'}
            </Typography>
            <IconButton
              size="small"
              onClick={() => setExpanded(null)}
              aria-label="Close expanded chart"
              sx={{ opacity: 0.7 }}
            >
              <CloseIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Box>
          {expanded === 'time' && (
            <ProcessingTimeChart chartTasks={chartTasks} stats={stats} height={280} />
          )}
          {expanded === 'density' && (
            <EntityDensityChart chartTasks={chartTasks} stats={stats} height={280} />
          )}
        </Box>
      )}

      {/* Context utilization — pinned last as a quiet single-bar summary of
          per-call context-window usage. The label + bar carry the rest of the
          detail in hover tooltips so the resting state stays clean. */}
      <Box sx={{ mt: 1.5 }}>
        <Tooltip
          title="How much of the model's context window each extraction call used. Hover the bar for the planned token budget (system prompt, input chunks, output, buffer) and the actual min/avg/max usage."
          arrow
        >
          <Typography
            variant="overline"
            sx={{
              display: 'inline-block',
              mb: 0.5,
              fontSize: '0.62rem',
              letterSpacing: 1,
              opacity: 0.7,
              cursor: 'help',
            }}
          >
            ⚡ Context utilization
          </Typography>
        </Tooltip>
        <ContextUtilizationChart tasks={tasks} stats={stats} />
      </Box>
    </Box>
  );
}
