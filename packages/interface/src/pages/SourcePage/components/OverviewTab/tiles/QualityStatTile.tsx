// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Divider, Typography } from '@mui/material';
import QualityIcon from '@mui/icons-material/EmojiEvents';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import type { SourceQualityScore } from '../../../../../types';
import { StatTile } from '../../../../../components/sources/StatTile';

interface QualityStatTileProps {
  qualityLoading: boolean;
  qualityScore: SourceQualityScore | null;
  hasScore: boolean;
  gradeColor: string;
  totalFiltered: number;
  entitiesFiltered: number;
  relsFiltered: number;
  totalInvalid: number;
  /** True when the source was extracted with pypdf (PDF source type). */
  isPypdf: boolean;
  /**
   * Called when the tile is clicked. Opens the QualityBreakdownDialog owned
   * by OverviewTab — the small tile is a summary; clicking drills in.
   */
  onClick?: () => void;
}

export function QualityStatTile({
  qualityLoading,
  qualityScore,
  hasScore,
  gradeColor,
  totalFiltered,
  entitiesFiltered,
  relsFiltered,
  totalInvalid,
  isPypdf,
  onClick,
}: QualityStatTileProps) {
  return (
    <StatTile
      value={
        qualityLoading
          ? '...'
          : hasScore && qualityScore
          ? qualityScore.quality_grade.toFixed(0)
          : 'N/A'
      }
      label={hasScore && qualityScore ? qualityScore.quality_label : 'Quality'}
      color={gradeColor}
      icon={<QualityIcon fontSize="inherit" />}
      onClick={onClick}
      tooltip={
        <Box sx={{ p: 0.5 }}>
          <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 600 }}>
            Quality
          </Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: 'auto auto', gap: 0.5, rowGap: 0.25 }}>
            {hasScore && qualityScore && (
              <>
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>Grade:</Typography>
                <Typography variant="caption" sx={{ fontWeight: 600 }}>
                  {qualityScore.quality_grade.toFixed(1)} ({qualityScore.quality_label})
                </Typography>
              </>
            )}
            {totalFiltered > 0 && (
              <>
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>Filtered:</Typography>
                <Typography variant="caption" sx={{ fontWeight: 600 }}>
                  {entitiesFiltered} entities, {relsFiltered} relationships
                </Typography>
              </>
            )}
            {totalInvalid > 0 && (
              <>
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>Invalid:</Typography>
                <Typography variant="caption" sx={{ fontWeight: 600 }}>{totalInvalid} unmatched relationships</Typography>
              </>
            )}
          </Box>
          {isPypdf && (
            <>
              <Divider sx={{ my: 1 }} />
              <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.75 }}>
                <InfoOutlinedIcon sx={{ fontSize: 14, mt: 0.15, color: 'info.main', flexShrink: 0 }} />
                <Typography variant="caption" sx={{ color: 'text.secondary', lineHeight: 1.5 }}>
                  PDF text was extracted with pypdf, which preserves prose but loses heading
                  structure. The lower structure score reflects this — answers will still be
                  accurate, but section-aware features (auto-generated outlines, heading-anchored
                  quotes) may be limited.
                </Typography>
              </Box>
            </>
          )}
        </Box>
      }
    />
  );
}
