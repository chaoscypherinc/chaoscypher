// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from 'react';
import { Box } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import type { Source, SourceStats } from '../../../../../types';
import { isSourceExtracted } from '../../../../../types';
import { surfaceSx, surfaceHoverSx } from '../../../../../theme/cardStyles';
import { useLLMProcessing } from '../../pipeline/hooks/useLLMProcessing';
import { useVisionPages } from '../../../../../services/api/useVisionPages';
import { PipelineFunnel } from '../../pipeline/PipelineFunnel';
import { ExtractionCounters } from '../../pipeline/details/ExtractionCounters';
import { PipelineStatCards } from './PipelineStatCards';
import { buildPipelineStats } from './pipelineStats';

interface PipelineFlowSectionProps {
  source: Source;
  stats: SourceStats | null;
}

const HEADER_SX = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  fontSize: '0.68rem',
  letterSpacing: 1.2,
  textTransform: 'uppercase',
  color: '#8893a0',
  fontWeight: 600,
} as const;

/**
 * Collapsible "how the pipeline ran" section on the Overview tab. Styled to
 * recede beneath the hero tiles (faint neutral border, no fill). Collapsed
 * (default) the whole box is the expand button and shows the muted pipeline
 * stat cards; expanded it reveals the full PipelineFunnel + the per-source
 * ExtractionCounters. The per-chunk stats and vision queries are gated on
 * `expanded`, so a collapsed section does no extra fetching. Self-hides when
 * there are no pipeline numbers to show.
 */
export function PipelineFlowSection({ source, stats }: PipelineFlowSectionProps) {
  const [expanded, setExpanded] = useState(false);
  const extracted = isSourceExtracted(source);
  const { state: llm } = useLLMProcessing(source.id, extracted && expanded);
  const { data: visionData } = useVisionPages(source.id, { enabled: expanded });

  const pipelineStats = buildPipelineStats(source, stats);
  if (pipelineStats.length === 0) return null;

  const chevron = (
    <ExpandMoreIcon
      sx={{ fontSize: 18, transition: 'transform 0.2s', transform: expanded ? 'rotate(180deg)' : 'none' }}
    />
  );

  // Collapsed: the entire box is the expand button (stat cards are non-interactive).
  if (!expanded) {
    return (
      <Box
        component="button"
        type="button"
        onClick={() => setExpanded(true)}
        aria-label="Pipeline flow, expand"
        aria-expanded={false}
        sx={{
          all: 'unset',
          display: 'block',
          boxSizing: 'border-box',
          width: '100%',
          mb: 3,
          ...surfaceSx,
          p: 1.5,
          cursor: 'pointer',
          transition: 'background 0.15s, border-color 0.15s',
          '&:hover': surfaceHoverSx,
          '&:focus-visible': { outline: '2px solid rgba(126,179,212,0.5)', outlineOffset: 2 },
        }}
      >
        <Box sx={HEADER_SX}>
          <span>Pipeline flow</span>
          {chevron}
        </Box>
        <Box sx={{ mt: 1.25 }}>
          <PipelineStatCards stats={pipelineStats} />
        </Box>
      </Box>
    );
  }

  // Expanded: the header row is the collapse toggle; funnel + counters render below.
  return (
    <Box sx={{ mb: 3, ...surfaceSx, p: 1.5 }}>
      <Box
        component="button"
        type="button"
        onClick={() => setExpanded(false)}
        aria-label="Pipeline flow, collapse"
        aria-expanded
        sx={{
          all: 'unset',
          display: 'block',
          width: '100%',
          cursor: 'pointer',
          '&:focus-visible': { outline: '2px solid rgba(126,179,212,0.5)', outlineOffset: 2 },
        }}
      >
        <Box sx={HEADER_SX}>
          <span>Pipeline flow</span>
          {chevron}
        </Box>
      </Box>
      <Box sx={{ mt: 1.25 }}>
        <PipelineFunnel source={source} llmStats={llm.stats} visionJob={visionData?.job ?? null} />
        <ExtractionCounters source={source} stats={llm.stats} />
      </Box>
    </Box>
  );
}
