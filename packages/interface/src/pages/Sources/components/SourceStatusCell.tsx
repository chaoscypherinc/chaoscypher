// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Source Status Cell Component
 *
 * Renders the unified status display for a source row, handling all
 * possible states: paused, error, active/completed, MCP extracting,
 * and processing (with segmented progress bar).
 */

import type { ReactNode } from 'react';
import {
  Box,
  Chip,
  Stack,
  Typography,
  Tooltip,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import ErrorIcon from '@mui/icons-material/Error';
import HubIcon from '@mui/icons-material/Hub';
import FactCheckIcon from '@mui/icons-material/FactCheck';
import PauseIcon from '@mui/icons-material/Pause';
import AutorenewIcon from '@mui/icons-material/Autorenew';
import type { UnifiedSource, SourceQualityScore } from '../../../types';
import { ScoreBadge } from '../../../components/Quality';
import { ContentTypeColors } from '../../../theme/colors';
import { ProcessingStatus } from './SourceProcessingStatus';
import { useRecoveryThresholds } from '../../../config/recoveryThresholds';
import { deriveMergedChipState } from '../../../components/sources/mergedChipState';
interface SourceStatusCellProps {
  /** The source to display status for. */
  source: UnifiedSource;
  /** Quality scores per source. */
  qualityScores?: Map<string, SourceQualityScore>;
  /** Open the confirm-domain dialog for a parked (awaiting_confirmation) source. */
  onConfirmExtraction?: (source: UnifiedSource) => void;
}

/** Small inline badge shown when a source has been auto-recovered at least once. */
function RecoveryBadge({ attempts }: { attempts: number }) {
  const { warnThreshold } = useRecoveryThresholds();
  const isWarning = attempts >= warnThreshold;
  const tooltipText = `Auto-recovered ${attempts} time${attempts === 1 ? '' : 's'}`;
  return (
    <Tooltip title={tooltipText} arrow>
      <Box
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          height: 20,
          px: 0.75,
          borderRadius: 1,
          gap: 0.4,
          border: `1px solid`,
          borderColor: isWarning ? 'warning.main' : 'info.dark',
          color: isWarning ? 'warning.main' : 'info.main',
          cursor: 'default',
        }}
      >
        <AutorenewIcon sx={{ fontSize: 12 }} />
        <Typography sx={{ fontSize: '0.7rem', fontWeight: 600, lineHeight: 1 }}>
          {attempts}
        </Typography>
      </Box>
    </Tooltip>
  );
}

/**
 * Unified status display combining stage, status, and progress.
 *
 * Renders different layouts depending on source state:
 * - Paused: warning chip with underlying status
 * - Error: error chip with message
 * - Active: active/disabled chip with graph stats and quality badge
 * - MCP Extracting: chunk progress with stale detection
 * - Processing: segmented progress bar with time estimate
 */
export function SourceStatusCell({ source, qualityScores, onConfirmExtraction }: SourceStatusCellProps) {
  const recoveryBadge = (source.recovery_attempts ?? 0) > 0
    ? <RecoveryBadge attempts={source.recovery_attempts!} />
    : null;

  // Paused state (badge is inlined inside PausedStatus)
  if (source.is_paused) {
    return <PausedStatus source={source} />;
  }

  // Error state (badge is inlined inside ErrorStatus)
  if (source.status === 'error') {
    return <ErrorStatus source={source} />;
  }

  // Active/completed state (badge is inlined inside ActiveStatus)
  if (source.stage === 'active') {
    return (
      <ActiveStatus
        source={source}
        qualityScores={qualityScores}
        recoveryBadge={recoveryBadge}
      />
    );
  }

  // Awaiting human confirmation of the detected domain — actionable chip,
  // never the processing spinner (which would imply work is in flight).
  if (source.status === 'awaiting_confirmation') {
    return (
      <AwaitingConfirmationStatus source={source} onConfirm={onConfirmExtraction} />
    );
  }

  // Processing states (including mcp_extracting) — segmented progress bar + stage tiles
  return (
    <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
      <ProcessingStatus source={source} />
      {recoveryBadge}
    </Stack>
  );
}

/** Paused state display. */
function PausedStatus({
  source,
}: {
  source: UnifiedSource;
}) {
  return (
    <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
      <Tooltip title={source.paused_reason ? `Paused: ${source.paused_reason}` : 'Processing paused'}>
        <Chip
          label="Paused"
          size="small"
          color="warning"
          icon={<PauseIcon sx={{ fontSize: 14 }} />}
          variant="outlined"
        />
      </Tooltip>
      <Typography variant="caption" sx={{ color: 'text.secondary' }}>
        was {source.status}
      </Typography>
      {(source.recovery_attempts ?? 0) > 0 && (
        <RecoveryBadge attempts={source.recovery_attempts!} />
      )}
    </Stack>
  );
}

/** Awaiting-confirmation state: actionable "Confirm domain" chip. */
function AwaitingConfirmationStatus({
  source,
  onConfirm,
}: {
  source: UnifiedSource;
  onConfirm?: (source: UnifiedSource) => void;
}) {
  const top = source.detection_ranking?.[0]?.domain;
  const lowConfidence = source.detection_low_confidence === true || !top;
  const tooltip = lowConfidence
    ? "Detection wasn't confident — pick a domain"
    : `Detected: ${top} — confirm or change before extraction`;
  return (
    <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
      <Tooltip title={tooltip} arrow>
        {/* Wrap in span so Tooltip's aria-label lands on the span,
            leaving the Chip's accessible name derived from its label. */}
        <span>
          <Chip
            label="Confirm domain"
            size="small"
            color="info"
            icon={<FactCheckIcon sx={{ fontSize: 14 }} />}
            variant="outlined"
            clickable
            onClick={(e) => {
              e.stopPropagation();
              onConfirm?.(source);
            }}
          />
        </span>
      </Tooltip>
      {!lowConfidence && (
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          {top}
        </Typography>
      )}
    </Stack>
  );
}

/** Error state display. */
function ErrorStatus({
  source,
}: {
  source: UnifiedSource;
}) {
  return (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1 }}>
      <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
        <Chip
          label="Error"
          size="small"
          color="error"
          icon={<ErrorIcon sx={{ fontSize: 14 }} />}
        />
        {(source.recovery_attempts ?? 0) > 0 && (
          <RecoveryBadge attempts={source.recovery_attempts!} />
        )}
      </Stack>
      {source.ingestion?.error_message && (
        <Typography
          variant="caption"
          color="error"
          sx={{ flex: '1 1 auto', minWidth: 150 }}
        >
          {source.ingestion.error_message}
        </Typography>
      )}
    </Box>
  );
}

/** Active/completed state display with graph stats and quality badge. */
function ActiveStatus({
  source,
  qualityScores,
  recoveryBadge,
}: {
  source: UnifiedSource;
  qualityScores?: Map<string, SourceQualityScore>;
  recoveryBadge?: ReactNode;
}) {
  const chunks = source.active?.chunk_count || 0;
  const entities = source.active?.nodes_created || 0;
  const relationships = source.active?.edges_created || 0;
  const templates = source.active?.templates_created || 0;
  const isEnabled = source.active?.enabled !== false;
  const graphTotal = entities + relationships + templates;
  const hubColor = ContentTypeColors.entities;
  const chip = deriveMergedChipState(
    isEnabled,
    source.vector_indexing_status,
    source.vector_indexed_at,
  );

  const statsTooltip = (
    <Box sx={{ p: 0.5 }}>
      <Typography variant="caption" sx={{ display: 'block', fontWeight: 600, mb: 0.25 }}>
        Knowledge Graph
      </Typography>
      <Typography variant="caption" sx={{ display: 'block' }}>
        Chunks: {chunks}
      </Typography>
      {entities > 0 && (
        <Typography variant="caption" sx={{ display: 'block' }}>
          Entities: {entities}
        </Typography>
      )}
      {relationships > 0 && (
        <Typography variant="caption" sx={{ display: 'block' }}>
          Relationships: {relationships}
        </Typography>
      )}
      {templates > 0 && (
        <Typography variant="caption" sx={{ display: 'block' }}>
          Templates: {templates}
        </Typography>
      )}
    </Box>
  );

  const qualityScore = qualityScores?.get(source.id);

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: { xs: 'center', md: 'space-between' },
        gap: 1,
        flexWrap: 'wrap',
      }}
    >
      <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
        <Tooltip title={chip.tooltip} arrow>
          <Chip
            label={chip.label}
            size="small"
            color={chip.color}
            icon={<chip.Icon sx={{ fontSize: 14 }} />}
          />
        </Tooltip>
        {recoveryBadge}
      </Stack>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
        {/* Graph stats badge */}
        {graphTotal > 0 && (
          <Tooltip title={statsTooltip} arrow placement="top">
            <Box
              sx={{
                display: 'inline-flex',
                alignItems: 'center',
                height: 24,
                px: 0.75,
                borderRadius: 1,
                bgcolor: 'transparent',
                border: `1px solid ${alpha(hubColor, 0.4)}`,
                cursor: 'default',
                gap: 0.5,
              }}
            >
              <HubIcon sx={{ fontSize: 14, color: hubColor }} />
              <Typography sx={{ fontSize: '0.75rem', fontWeight: 600, color: hubColor, lineHeight: 1 }}>
                {graphTotal}
              </Typography>
            </Box>
          </Tooltip>
        )}
        {/* Quality Score Badge */}
        {qualityScore && (
          <ScoreBadge
            score={qualityScore.total_score}
            qualityGrade={qualityScore.quality_grade}
            qualityLabel={qualityScore.quality_label}
            size="small"
            avgEntityQuality={qualityScore.avg_entity_quality}
            avgRelationshipQuality={qualityScore.avg_relationship_quality}
            connectivityRatio={qualityScore.connectivity_ratio}
            showIcon
          />
        )}
      </Box>
    </Box>
  );
}
