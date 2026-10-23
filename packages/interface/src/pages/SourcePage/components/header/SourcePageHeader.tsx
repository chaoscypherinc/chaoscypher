// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Chip, IconButton, Tooltip, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import PauseIcon from '@mui/icons-material/Pause';
import type { Source } from '../../../../types';
import { isSourceCommitted } from '../../../../types';
import { DomainColors } from '../../../../theme/colors';
import { deriveMergedChipState } from '../../../../components/sources/mergedChipState';
import { PacksChip } from '../../../../components/sources/PacksChip';
import { InlineTagEditor } from '../InlineTagEditor';
import { FileInfoTooltip } from './FileInfoTooltip';
import { SourceActionsMenu } from './SourceActionsMenu';
import { getStatusColor, getStatusLabel } from './statusMeta';

interface SourcePageHeaderProps {
  source: Source;
  onBack: () => void;
  onToggleEnabled: () => void;
  onChat: () => void;
  onAbort: () => void;
  onDelete: () => void;
  onViewInGraph?: () => void;
  onPause?: () => void;
  onResume?: () => void;
  onRetry?: () => void;
  onReExtract?: (force: boolean) => void;
  /**
   * Audit fix #F49 — explicit Re-extract action distinct from Retry,
   * routed through ``POST /sources/{id}/re_extract``. Discards cached
   * extraction and re-runs the LLM.
   */
  onReextract?: () => void;
  /** Open the source's pack settings (re-extracts under the packs chosen there). */
  onChangePacks?: () => void;
}

/**
 * SourcePage header row: icon-only back button, status/domain chips,
 * title with file-info tooltip, inline tag editor (committed only),
 * and three-dot actions menu.
 */
export function SourcePageHeader({
  source,
  onBack,
  onToggleEnabled,
  onChat,
  onAbort,
  onDelete,
  onViewInGraph,
  onPause,
  onResume,
  onRetry,
  onReExtract,
  onReextract,
  onChangePacks,
}: SourcePageHeaderProps) {
  const domainColor = source.extraction_domain_auto
    ? DomainColors.auto
    : DomainColors.manual;

  return (
    <Box
      sx={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 1,
        justifyContent: 'space-between',
        alignItems: { xs: 'flex-start', sm: 'center' },
        mb: 2,
      }}
    >
      {/* Left: back + status + title */}
      <Box sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 2, minWidth: 0 }}>
        <IconButton aria-label="Back" onClick={onBack}>
          <ArrowBackIcon />
        </IconButton>
        {source.is_paused ? (
          <Tooltip title={source.paused_reason ? `Paused: ${source.paused_reason}` : 'Processing paused'}>
            <Chip
              label="Paused"
              size="small"
              color="warning"
              variant="outlined"
              icon={<PauseIcon sx={{ fontSize: 14 }} />}
            />
          </Tooltip>
        ) : isSourceCommitted(source) ? (
          // Merged Active / Search-status chip (2026-05-11). The
          // previously-separate SearchStatusBadge that lived in the
          // top-right of this header is now folded into this chip, so
          // a degraded / failed vector index turns the chip yellow /
          // red and explains itself via the tooltip rather than
          // sitting beside the title as its own pill.
          (() => {
            const chip = deriveMergedChipState(
              source.enabled,
              source.quality_metrics?.vector_indexing_status,
              source.quality_metrics?.vector_indexed_at,
            );
            return (
              <Tooltip title={chip.tooltip} arrow>
                <Chip
                  label={chip.label}
                  size="small"
                  color={chip.color}
                  icon={<chip.Icon sx={{ fontSize: 14 }} />}
                />
              </Tooltip>
            );
          })()
        ) : (
          <Chip
            label={getStatusLabel(source.status)}
            size="small"
            color={getStatusColor(source.status)}
          />
        )}
        {source.extraction_domain && (
          <Tooltip arrow title={<FileInfoTooltip source={source} />}>
            {/* span: the chip may render nothing, and a Tooltip needs one child */}
            <span>
              <PacksChip
                source={source}
                color={domainColor}
                suffix={source.extraction_domain_auto ? '' : ' (Custom)'}
              />
            </span>
          </Tooltip>
        )}
        <Tooltip arrow title={<FileInfoTooltip source={source} />}>
          <Typography variant="h5" noWrap sx={{ cursor: 'default' }}>
            {source.title || source.filename}
          </Typography>
        </Tooltip>
      </Box>

      {/* Right: tags + actions menu. The vector-search status badge
          that used to sit here was merged into the Active chip on the
          left (2026-05-11) so the index health rides with the
          visibility signal. */}
      <Box
        sx={{
          display: 'flex',
          gap: 1,
          alignItems: 'center',
          flexShrink: 0,
        }}
      >
        {isSourceCommitted(source) && <InlineTagEditor sourceId={source.id} />}
        <SourceActionsMenu
          source={source}
          onToggleEnabled={onToggleEnabled}
          onChat={onChat}
          onAbort={onAbort}
          onDelete={onDelete}
          onViewInGraph={onViewInGraph}
          onPause={onPause}
          onResume={onResume}
          onRetry={onRetry}
          onReExtract={onReExtract}
          onReextract={onReextract}
          onChangePacks={onChangePacks}
        />
      </Box>
    </Box>
  );
}
