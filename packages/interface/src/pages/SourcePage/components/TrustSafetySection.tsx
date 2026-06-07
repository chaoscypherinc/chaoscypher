// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TrustSafetySection: Displays the trust safety (pollution penalty) status.
 *
 * Renders either a penalty warning when low-quality items exceed the
 * threshold, or a clean status indicator when the data is healthy.
 */

import React, { memo } from 'react';
import { Box, Typography } from '@mui/material';
import TrustIcon from '@mui/icons-material/Shield';
import CleanIcon from '@mui/icons-material/CheckCircle';
import WarningIcon from '@mui/icons-material/Warning';
import { SECTION_COLORS } from '../../../components/Quality/utils';
import { SectionCard } from '../../../components/Quality/sections';
import { TrustSafetyTooltip } from './QualityTooltips';

interface TrustSafetySectionProps {
  /** Pollution penalty value (low-quality item inflation, 0 = clean). */
  pollutionPenalty: number;
  /** Structural penalty value (hub-skew + reciprocal-rate, v7+). */
  structuralPenalty?: number;
  /** Hub skew ratio (max_degree / median_degree). */
  hubSkew?: number;
  /** Reciprocal rate (0-1) — same-type reciprocal edges. */
  reciprocalRate?: number;
  /** Count of low-quality entities. */
  lowQualityEntityCount: number;
  /** Count of low-quality relationships. */
  lowQualityRelationshipCount: number;
}

/**
 * Renders a trust safety card showing pollution and structural penalties, or
 * a clean data status when both are zero.
 */
const TrustSafetySectionComponent: React.FC<TrustSafetySectionProps> = ({
  pollutionPenalty,
  structuralPenalty = 0,
  hubSkew = 1,
  reciprocalRate = 0,
  lowQualityEntityCount,
  lowQualityRelationshipCount,
}) => {
  const totalPenalty = pollutionPenalty + structuralPenalty;

  if (totalPenalty > 0) {
    return (
      <Box sx={{ mb: 2 }}>
        <SectionCard
          title="Trust Safety"
          icon={<TrustIcon fontSize="small" />}
          color={SECTION_COLORS.penalty}
          headerValue={`-${totalPenalty.toFixed(0)} pts`}
          tooltip={<TrustSafetyTooltip />}
        >
          {pollutionPenalty > 0 && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <WarningIcon sx={{ color: SECTION_COLORS.penalty, fontSize: 18 }} />
              <Typography variant="body2" sx={{ color: SECTION_COLORS.penalty }}>
                Pollution ({pollutionPenalty.toFixed(0)} pts):{' '}
                {lowQualityEntityCount + lowQualityRelationshipCount} items with score {'<'} 40
              </Typography>
            </Box>
          )}
          {structuralPenalty > 0 && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: pollutionPenalty > 0 ? 0.75 : 0 }}>
              <WarningIcon sx={{ color: SECTION_COLORS.penalty, fontSize: 18 }} />
              <Typography variant="body2" sx={{ color: SECTION_COLORS.penalty }}>
                Structural ({structuralPenalty.toFixed(0)} pts):
                {' '}hub skew {hubSkew.toFixed(1)}x
                {reciprocalRate > 0 ? `, ${(reciprocalRate * 100).toFixed(0)}% reciprocal edges` : ''}
              </Typography>
            </Box>
          )}
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              mt: 1,
              display: 'block'
            }}>
            Pollution flags low-quality items. Structural flags padded graphs —
            one entity over-connected to everything, or duplicate edges in both directions.
          </Typography>
        </SectionCard>
      </Box>
    );
  }

  return (
    <Box sx={{ mb: 2 }}>
      <SectionCard
        title="Trust Safety"
        icon={<TrustIcon fontSize="small" />}
        color={SECTION_COLORS.connectivity}
        headerValue="Clean"
        tooltip={<TrustSafetyTooltip />}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <CleanIcon sx={{ color: SECTION_COLORS.connectivity, fontSize: 18 }} />
          <Typography variant="body2" sx={{ color: SECTION_COLORS.connectivity }}>
            Clean Data: No penalties applied
          </Typography>
        </Box>
        <Typography
          variant="caption"
          sx={{
            color: "text.secondary",
            mt: 1,
            display: 'block'
          }}>
          No low-quality items, no hub-skew, no reciprocal bloat.
        </Typography>
      </SectionCard>
    </Box>
  );
};

export const TrustSafetySection = memo(TrustSafetySectionComponent);
