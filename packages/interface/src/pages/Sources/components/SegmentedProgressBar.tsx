// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography, Tooltip, keyframes } from '@mui/material';
import CompleteIcon from '@mui/icons-material/CheckCircle';
import PendingIcon from '@mui/icons-material/RadioButtonUnchecked';
import ActiveIcon from '@mui/icons-material/Pending';
import { STAGE_WEIGHTS, type StageName } from '../utils/progressCalculation';
import { StageColors } from '../../../theme/colors';

interface SegmentedProgressBarProps {
  /** Total progress across all stages (0-100) */
  totalProgress: number;
  /** Current stage name */
  stageName: StageName;
  /** Progress within current stage (0-100) */
  stageProgress: number;
  /** Height of the progress bar */
  height?: number;
  /** Gap between segments */
  gap?: number;
  /** Show labels under each segment */
  showLabels?: boolean;
  /** Reserve vertical space for title (for alignment) without showing text */
  reserveTitleSpace?: boolean;
  /** Disable internal tooltip (use when parent has tooltip) */
  disableTooltip?: boolean;
}

// Stage colors
const STAGE_COLORS = {
  indexing: StageColors.indexing,
  extraction: StageColors.extraction,
  commit: StageColors.commit,
  empty: StageColors.empty,
};

// Breathing animation for active stage
const breathe = keyframes`
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
`;

/**
 * Get the color for a stage based on its active/complete state.
 */
function getStageColor(isActive: boolean, isComplete: boolean, activeColor: string): string {
  if (isActive || isComplete) return activeColor;
  return 'text.secondary';
}

/**
 * Get the fill width for a progress segment.
 */
function getSegmentFill(isComplete: boolean, isActive: boolean, progress: number): string {
  if (isComplete) return '100%';
  if (isActive) return `${progress}%`;
  return '0%';
}

/**
 * Get the prefix icon for a stage label.
 */
function getStagePrefix(isComplete: boolean, isActive: boolean): string {
  if (isComplete) return '\u2713 '; // checkmark
  if (isActive) return '... ';
  return '';
}

// Stage descriptions for tooltip
const STAGE_INFO = {
  indexing: {
    name: 'Index',
    description: 'Chunking document and generating embeddings for search',
  },
  extraction: {
    name: 'Extract',
    description: 'AI analyzes each chunk to extract entities and relationships',
  },
  commit: {
    name: 'Commit',
    description: 'Saving extracted knowledge to the graph database',
  },
};

/** Tooltip content showing stage descriptions with current position */
export function StageTooltipContent({
  isIndexingComplete,
  isExtractionComplete,
  isCommitComplete,
  isIndexingActive,
  isExtractionActive,
  isCommitActive,
}: {
  isIndexingComplete: boolean;
  isExtractionComplete: boolean;
  isCommitComplete: boolean;
  isIndexingActive: boolean;
  isExtractionActive: boolean;
  isCommitActive: boolean;
}) {
  const getIcon = (isComplete: boolean, isActive: boolean, color: string) => {
    if (isComplete) return <CompleteIcon sx={{ fontSize: 14, color, mr: 0.5 }} />;
    if (isActive) return <ActiveIcon sx={{ fontSize: 14, color, mr: 0.5 }} />;
    return <PendingIcon sx={{ fontSize: 14, color: 'text.disabled', mr: 0.5 }} />;
  };

  const getStatus = (isComplete: boolean, isActive: boolean) => {
    if (isComplete) return 'Complete';
    if (isActive) return 'In Progress';
    return 'Pending';
  };

  return (
    <Box sx={{ p: 0.5, minWidth: 220 }}>
      <Typography variant="caption" sx={{ fontWeight: 'bold', display: 'block', mb: 1 }}>
        Processing Stages
      </Typography>
      {/* Indexing */}
      <Box sx={{ display: 'flex', alignItems: 'flex-start', mb: 0.75 }}>
        {getIcon(isIndexingComplete, isIndexingActive, STAGE_COLORS.indexing)}
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 500, color: getStageColor(isIndexingActive, isIndexingComplete, STAGE_COLORS.indexing) }}>
            1. {STAGE_INFO.indexing.name}
            <Typography component="span" variant="caption" sx={{ ml: 0.5, color: 'text.disabled', fontSize: '0.65rem' }}>
              ({getStatus(isIndexingComplete, isIndexingActive)})
            </Typography>
          </Typography>
          <Typography
            variant="caption"
            sx={{
              display: "block",
              color: "text.secondary",
              fontSize: '0.65rem'
            }}>
            {STAGE_INFO.indexing.description}
          </Typography>
        </Box>
      </Box>
      {/* Extraction */}
      <Box sx={{ display: 'flex', alignItems: 'flex-start', mb: 0.75 }}>
        {getIcon(isExtractionComplete, isExtractionActive, STAGE_COLORS.extraction)}
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 500, color: getStageColor(isExtractionActive, isExtractionComplete, STAGE_COLORS.extraction) }}>
            2. {STAGE_INFO.extraction.name}
            <Typography component="span" variant="caption" sx={{ ml: 0.5, color: 'text.disabled', fontSize: '0.65rem' }}>
              ({getStatus(isExtractionComplete, isExtractionActive)})
            </Typography>
          </Typography>
          <Typography
            variant="caption"
            sx={{
              display: "block",
              color: "text.secondary",
              fontSize: '0.65rem'
            }}>
            {STAGE_INFO.extraction.description}
          </Typography>
        </Box>
      </Box>
      {/* Commit */}
      <Box sx={{ display: 'flex', alignItems: 'flex-start' }}>
        {getIcon(isCommitComplete, isCommitActive, STAGE_COLORS.commit)}
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 500, color: getStageColor(isCommitActive, isCommitComplete, STAGE_COLORS.commit) }}>
            3. {STAGE_INFO.commit.name}
            <Typography component="span" variant="caption" sx={{ ml: 0.5, color: 'text.disabled', fontSize: '0.65rem' }}>
              ({getStatus(isCommitComplete, isCommitActive)})
            </Typography>
          </Typography>
          <Typography
            variant="caption"
            sx={{
              display: "block",
              color: "text.secondary",
              fontSize: '0.65rem'
            }}>
            {STAGE_INFO.commit.description}
          </Typography>
        </Box>
      </Box>
    </Box>
  );
}

/**
 * A segmented progress bar showing 3 stages: Indexing (10%), Extraction (75%), Commit (15%).
 *
 * Each segment fills based on progress:
 * - Completed stages: Fully colored
 * - Current stage: Partially filled with color, rest grey
 * - Future stages: Grey/unfilled
 */
export function SegmentedProgressBar({
  totalProgress,
  stageName,
  stageProgress,
  height = 6,
  gap = 2,
  showLabels = false,
  reserveTitleSpace = false,
  disableTooltip = false,
}: SegmentedProgressBarProps) {
  // Determine fill state for each segment
  const extractionEnd = STAGE_WEIGHTS.indexing + STAGE_WEIGHTS.extraction;

  const isIndexingComplete = totalProgress >= STAGE_WEIGHTS.indexing;
  const isExtractionComplete = totalProgress >= extractionEnd;
  const isCommitComplete = totalProgress >= 100;

  const isIndexingActive = stageName === 'indexing';
  const isExtractionActive = stageName === 'extraction';
  const isCommitActive = stageName === 'commit';

  // Calculate widths accounting for gaps
  const indexingWidth = STAGE_WEIGHTS.indexing;
  const extractionWidth = STAGE_WEIGHTS.extraction;
  const commitWidth = STAGE_WEIGHTS.commit;

  // Label styles
  const getLabelStyle = (isActive: boolean, isComplete: boolean, color: string) => ({
    fontSize: '0.65rem',
    color: getStageColor(isActive, isComplete, color),
    textAlign: 'center' as const,
    mt: 0.5,
    animation: isActive ? `${breathe} 2s ease-in-out infinite` : 'none',
    fontWeight: isActive ? 500 : 400,
  });

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

  return (
    <Box sx={{ width: '100%' }}>
      {/* Spacer for vertical alignment (same height as title would be) */}
      {reserveTitleSpace && (
        <Box sx={{ height: '0.6rem', mb: 0.25 }} />
      )}

      {/* Progress bar content */}
      {(() => {
        const progressBarContent = (
          <Box
            sx={{
              display: 'flex',
              height,
              width: '100%',
              gap: `${gap}px`,
              cursor: disableTooltip ? undefined : 'help',
            }}
          >
            {/* Indexing segment (10%) */}
            <Box
              sx={{
                width: `${indexingWidth}%`,
                bgcolor: STAGE_COLORS.empty,
                borderRadius: height / 2,
                position: 'relative',
                overflow: 'hidden',
              }}
            >
              <Box
                sx={{
                  position: 'absolute',
                  left: 0,
                  top: 0,
                  height: '100%',
                  width: getSegmentFill(isIndexingComplete, isIndexingActive, stageProgress),
                  bgcolor: STAGE_COLORS.indexing,
                  borderRadius: height / 2,
                  transition: 'width 0.3s ease',
                }}
              />
            </Box>

            {/* Extraction segment (75%) */}
            <Box
              sx={{
                width: `${extractionWidth}%`,
                bgcolor: STAGE_COLORS.empty,
                borderRadius: height / 2,
                position: 'relative',
                overflow: 'hidden',
              }}
            >
              <Box
                sx={{
                  position: 'absolute',
                  left: 0,
                  top: 0,
                  height: '100%',
                  width: getSegmentFill(isExtractionComplete, isExtractionActive, stageProgress),
                  bgcolor: STAGE_COLORS.extraction,
                  borderRadius: height / 2,
                  transition: 'width 0.3s ease',
                }}
              />
            </Box>

            {/* Commit segment (15%) */}
            <Box
              sx={{
                width: `${commitWidth}%`,
                bgcolor: STAGE_COLORS.empty,
                borderRadius: height / 2,
                position: 'relative',
                overflow: 'hidden',
              }}
            >
              <Box
                sx={{
                  position: 'absolute',
                  left: 0,
                  top: 0,
                  height: '100%',
                  width: getSegmentFill(isCommitComplete, isCommitActive, stageProgress),
                  bgcolor: STAGE_COLORS.commit,
                  borderRadius: height / 2,
                  transition: 'width 0.3s ease',
                }}
              />
            </Box>
          </Box>
        );

        return disableTooltip ? (
          progressBarContent
        ) : (
          <Tooltip title={tooltipContent} arrow placement="top">
            {progressBarContent}
          </Tooltip>
        );
      })()}

      {/* Labels */}
      {showLabels && (
        <Box
          sx={{
            display: 'flex',
            width: '100%',
            gap: `${gap}px`,
          }}
        >
          <Typography
            sx={{
              width: `${indexingWidth}%`,
              ...getLabelStyle(isIndexingActive, isIndexingComplete, STAGE_COLORS.indexing),
            }}
          >
            {getStagePrefix(isIndexingComplete, isIndexingActive)}Index
          </Typography>
          <Typography
            sx={{
              width: `${extractionWidth}%`,
              ...getLabelStyle(isExtractionActive, isExtractionComplete, STAGE_COLORS.extraction),
            }}
          >
            {getStagePrefix(isExtractionComplete, isExtractionActive)}Extract
          </Typography>
          <Typography
            sx={{
              width: `${commitWidth}%`,
              ...getLabelStyle(isCommitActive, isCommitComplete, STAGE_COLORS.commit),
            }}
          >
            {getStagePrefix(isCommitComplete, isCommitActive)}Commit
          </Typography>
        </Box>
      )}
    </Box>
  );
}
