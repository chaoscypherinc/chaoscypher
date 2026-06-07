// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Source Processing Status Components
 *
 * Status display for sources that are currently being processed:
 * standard processing with segmented progress bar and one consolidated
 * time/progress slot fed by ``stage_progress`` when an LLM stage is
 * active.
 */

import {
  Box,
  Chip,
  Typography,
  Tooltip,
  CircularProgress,
} from '@mui/material';
import type { UnifiedSource } from '../../../types';
import { StageColors, StatusColors } from '../../../theme/colors';
import {
  calculateSegmentedProgress,
  getActiveStageProgress,
  STAGE_WEIGHTS,
} from '../utils/progressCalculation';
import { SegmentedProgressBar, StageTooltipContent } from './SegmentedProgressBar';
import { LLMStageTooltip } from '../../../components/LLMStageProgress';
import { formatDurationNullable } from '../../../utils/formatters';

interface ProcessingStatusProps {
  /** The source being processed. */
  source: UnifiedSource;
}

/**
 * Standard processing state with segmented progress bar and stage tiles.
 *
 * Shows a status chip with spinner, a three-segment progress bar
 * (index/extract/commit), stage-level progress tiles from stage_progress,
 * and an estimated time remaining display sourced from avg_ms.
 */
const STAGE_DISPLAY_LABEL: Record<string, string> = {
  vision: 'Vision processing',
  embedding: 'Embedding',
  mcp_extraction: 'MCP Entity Extraction',
};

export function ProcessingStatus({ source }: ProcessingStatusProps) {
  const progress = calculateSegmentedProgress(source);
  const stepDescription = source.ingestion?.step_description || progress.stageLabel;

  // Calculate stage completion states for tooltip
  const extractionEnd = STAGE_WEIGHTS.indexing + STAGE_WEIGHTS.extraction;
  const isIndexingComplete = progress.totalProgress >= STAGE_WEIGHTS.indexing;
  const isExtractionComplete = progress.totalProgress >= extractionEnd;
  const isCommitComplete = progress.totalProgress >= 100;
  const isIndexingActive = progress.stageName === 'indexing';
  const isExtractionActive = progress.stageName === 'extraction';
  const isCommitActive = progress.stageName === 'commit';

  const tooltipContent = (
    <StageTooltipContent
      isIndexingComplete={isIndexingComplete}
      isExtractionComplete={isExtractionComplete}
      isCommitComplete={isCommitComplete}
      isIndexingActive={isIndexingActive}
      isExtractionActive={isExtractionActive}
      isCommitActive={isCommitActive}
    />
  );

  // Stage colors for chip
  const stageColors: Record<string, string> = {
    indexing: StageColors.indexing,
    extraction: StageColors.extraction,
    commit: StageColors.commit,
  };
  const chipColor = stageColors[progress.stageName] || StatusColors.neutral;

  // One consolidated time/progress slot, top-right. When an LLM stage is
  // active (vision / embedding / mcp_extraction), surface its live
  // X/Y caption + EMA-derived remaining, wrapped in the rich LLMStageTooltip
  // so hovering shows avg-per-item plus stage-specific extras (entities /
  // relationships previews). When no LLM stage is active (early loader
  // phase, commit phase), fall back to the rolled-up "Remaining ~Nm"
  // estimate from calculateSegmentedProgress.
  const activeStage = getActiveStageProgress(source);
  const activeStageLabel = activeStage ? STAGE_DISPLAY_LABEL[activeStage.stageName] ?? activeStage.stageName : null;
  const activeRemainingText =
    activeStage?.remainingSeconds != null
      ? formatDurationNullable(activeStage.remainingSeconds)
      : null;
  const fallbackRemainingText =
    !activeStage && progress.estimatedRemainingSeconds
      ? `~${Math.ceil(progress.estimatedRemainingSeconds / 60)}m`
      : null;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, width: '100%' }}>
      <Tooltip title={tooltipContent} arrow placement="top">
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: 1.5, cursor: 'help' }}>
          {/* Left: status chip with spinner */}
          <Chip
            icon={<CircularProgress size={12} thickness={4} sx={{ color: chipColor }} />}
            label={stepDescription}
            size="small"
            sx={{
              height: 22,
              fontSize: '0.7rem',
              bgcolor: `${chipColor}20`,
              color: chipColor,
              borderColor: chipColor,
              '& .MuiChip-icon': { ml: 0.5, color: chipColor },
              '& .MuiChip-label': { px: 1 },
              minWidth: 'fit-content',
              maxWidth: 160,
            }}
            variant="outlined"
          />

          {/* Middle: segmented progress bar */}
          <Box sx={{ flex: 1 }}>
            <SegmentedProgressBar
              totalProgress={progress.totalProgress}
              stageName={progress.stageName}
              stageProgress={progress.stageProgress}
              height={6}
              showLabels
              reserveTitleSpace
              disableTooltip
            />
          </Box>

          {/* Right: single consolidated time + per-stage progress slot. */}
          {activeStage ? (
            <Tooltip
              arrow
              placement="top"
              title={
                <LLMStageTooltip
                  label={activeStageLabel ?? activeStage.stageName}
                  processed={activeStage.processed}
                  total={activeStage.total}
                  itemNoun={activeStage.itemNoun}
                  avgMs={activeStage.avgMs}
                >
                  {activeStage.record.extras?.entities_preview != null && (
                    <Typography variant="caption" sx={{ display: 'block' }}>
                      Entities found: {String(activeStage.record.extras.entities_preview)}
                    </Typography>
                  )}
                  {activeStage.record.extras?.relationships_preview != null && (
                    <Typography variant="caption" sx={{ display: 'block' }}>
                      Relationships found: {String(activeStage.record.extras.relationships_preview)}
                    </Typography>
                  )}
                </LLMStageTooltip>
              }
            >
              <Box sx={{ minWidth: 90, textAlign: 'right', cursor: 'help' }}>
                <Typography
                  variant="caption"
                  sx={{ color: 'text.disabled', fontSize: '0.6rem', display: 'block', lineHeight: 1 }}
                >
                  {activeStage.processed}/{activeStage.total} {activeStage.itemNoun}
                </Typography>
                <Typography variant="caption" noWrap sx={{ color: 'text.secondary' }}>
                  {activeRemainingText ? `~${activeRemainingText}` : '—'}
                </Typography>
              </Box>
            </Tooltip>
          ) : fallbackRemainingText ? (
            <Box sx={{ minWidth: 70, textAlign: 'right' }}>
              <Typography
                variant="caption"
                sx={{ color: 'text.disabled', fontSize: '0.6rem', display: 'block', lineHeight: 1 }}
              >
                Remaining
              </Typography>
              <Typography variant="caption" noWrap sx={{ color: 'text.secondary' }}>
                {fallbackRemainingText}
              </Typography>
            </Box>
          ) : null}
        </Box>
      </Tooltip>
    </Box>
  );
}
