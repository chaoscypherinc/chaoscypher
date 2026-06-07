// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * QualityMetricSection: Individual metric sections for the extraction quality card.
 *
 * Renders the full 2x2 grid of metric cards, trust safety section, final
 * grade calculation, and a recalculate button. Tooltip content and the
 * trust safety section are delegated to sub-components.
 */

import {
  Box,
  Tooltip,
  IconButton,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import EntityIcon from '@mui/icons-material/AccountTree';
import RelationshipIcon from '@mui/icons-material/Link';
import TopologyIcon from '@mui/icons-material/Hub';
import CalculateIcon from '@mui/icons-material/Calculate';
import RichnessIcon from '@mui/icons-material/Insights';
import WarningIcon from '@mui/icons-material/Warning';
import type { SourceQualityScore } from '../../../types';
import { getGradeColor, formatRichness, SECTION_COLORS } from '../../../components/Quality/utils';
import { SectionCard, MetricProgress, CalculationRow, FinalGradeVisual } from '../../../components/Quality/sections';
import {
  EntityQualityTooltip,
  RelationshipQualityTooltip,
  TopologyTooltip,
  FinalGradeTooltip,
  RichnessTooltip,
} from './QualityTooltips';
import { TrustSafetySection } from './TrustSafetySection';

// ---------------------------------------------------------------------------
// Main QualityMetrics Component
// ---------------------------------------------------------------------------

interface QualityMetricsProps {
  /** The quality score data to render. */
  score: SourceQualityScore;
  /** Whether the score is currently being recalculated. */
  loading: boolean;
  /** Callback to trigger a recalculation. */
  onRecalculate: () => void;
}

/**
 * Renders the full quality metrics breakdown for an extraction.
 *
 * Displays a 2x2 grid of metric cards (Relationship Quality, Entity Quality,
 * Topology Score, Richness Score), a Trust Safety section, the Final Grade
 * calculation, and a recalculate button.
 */
export function QualityMetrics({ score, loading, onRecalculate }: QualityMetricsProps) {
  const gradeColor = getGradeColor(score.quality_label);
  const hasRelationships = score.relationship_count > 0;
  const relationshipContribution = score.avg_relationship_quality * 0.5;
  const entityContribution = score.avg_entity_quality * 0.35;
  const topologyContribution = (score.topology_score ?? 0) * 0.15;
  const pollutionPenalty = score.pollution_penalty ?? 0;
  const structuralPenalty = score.structural_penalty ?? 0;
  const hubSkew = score.hub_skew ?? 1;
  const reciprocalRate = score.reciprocal_rate ?? 0;
  const connectivityScore = score.connectivity_ratio * 100;
  const densityScore = score.density_score ?? 0;
  const densityRatio = score.density_ratio ?? 0;

  return (
    <Box sx={{ px: 2, pb: 2, pt: 2 }}>
      {/* 2x2 Grid for metric sections */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, 1fr)',
          gap: 2,
          mb: 2,
        }}
      >
        {/* Relationship Quality */}
        <SectionCard
          title="Relationship Quality"
          icon={<RelationshipIcon sx={{ fontSize: "small" }} />}
          color={SECTION_COLORS.relationship}
          headerValue={hasRelationships ? `${score.avg_relationship_quality.toFixed(1)}/100` : 'N/A'}
          weightLabel="Weight: 50%"
          tooltip={<RelationshipQualityTooltip />}
        >
          {hasRelationships ? (
            <>
              <MetricProgress
                value={score.avg_relationship_quality}
                label="Average score"
                suffix="/100"
                color={SECTION_COLORS.relationship}
              />
              <CalculationRow
                label="Contributes:"
                formula={`${score.avg_relationship_quality.toFixed(1)} x 0.5`}
                result={`= ${relationshipContribution.toFixed(1)} points`}
                color={SECTION_COLORS.relationship}
              />
              {(score.low_quality_relationship_count ?? 0) > 0 && (
                <Typography
                  variant="caption"
                  sx={{ color: SECTION_COLORS.penalty, display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.5 }}
                >
                  <WarningIcon sx={{ fontSize: 14 }} />
                  {score.low_quality_relationship_count} low-quality (score {'<'} 40)
                </Typography>
              )}
            </>
          ) : (
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              No relationships extracted. This limits the maximum grade to ~50 points.
            </Typography>
          )}
        </SectionCard>

        {/* Entity Quality */}
        <SectionCard
          title="Entity Quality"
          icon={<EntityIcon fontSize="small" />}
          color={SECTION_COLORS.entity}
          headerValue={`${score.avg_entity_quality.toFixed(1)}/100`}
          weightLabel="Weight: 35%"
          tooltip={<EntityQualityTooltip />}
        >
          <MetricProgress
            value={score.avg_entity_quality}
            label="Average score"
            suffix="/100"
            color={SECTION_COLORS.entity}
          />
          <CalculationRow
            label="Contributes:"
            formula={`${score.avg_entity_quality.toFixed(1)} x 0.35`}
            result={`= ${entityContribution.toFixed(1)} points`}
            color={SECTION_COLORS.entity}
          />
          {(score.low_quality_entity_count ?? 0) > 0 && (
            <Typography
              variant="caption"
              sx={{ color: SECTION_COLORS.penalty, display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.5 }}
            >
              <WarningIcon sx={{ fontSize: 14 }} />
              {score.low_quality_entity_count} low-quality (score {'<'} 40)
            </Typography>
          )}
        </SectionCard>

        {/* Topology Score */}
        <SectionCard
          title="Topology Score"
          icon={<TopologyIcon fontSize="small" />}
          color={SECTION_COLORS.connectivity}
          headerValue={`${(score.topology_score ?? 0).toFixed(0)}/100`}
          weightLabel="Weight: 15%"
          tooltip={<TopologyTooltip />}
        >
          <MetricProgress
            value={score.topology_score ?? 0}
            label="Graph structure"
            suffix="/100"
            color={SECTION_COLORS.connectivity}
          />
          <CalculationRow
            label="Connectivity:"
            result={`${connectivityScore.toFixed(0)}% entities connected`}
            color={SECTION_COLORS.connectivity}
          />
          <CalculationRow
            label="Density:"
            result={`${densityScore.toFixed(0)} (${densityRatio.toFixed(1)} edges/node)`}
            color={SECTION_COLORS.connectivity}
          />
          <CalculationRow
            label="Contributes:"
            formula={`${(score.topology_score ?? 0).toFixed(1)} x 0.15`}
            result={`= ${topologyContribution.toFixed(1)} points`}
            color={SECTION_COLORS.connectivity}
          />
        </SectionCard>

        {/* Richness Score */}
        <SectionCard
          title="Richness Score"
          icon={<RichnessIcon fontSize="small" />}
          color={SECTION_COLORS.richness}
          headerValue={formatRichness(score.total_score)}
          tooltip={<RichnessTooltip />}
        >
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              display: "block",
              mb: 1
            }}>
            Volume metric (unbounded)
          </Typography>
          <CalculationRow
            label="Entities:"
            result={`${score.entity_contribution.toFixed(0)} (${score.entity_count} items)`}
            color={SECTION_COLORS.entity}
          />
          <CalculationRow
            label="Relationships:"
            result={`${score.relationship_contribution.toFixed(0)} (${score.relationship_count} items)`}
            color={SECTION_COLORS.relationship}
          />
          <CalculationRow
            label="Connectivity:"
            result={`+${score.connectivity_bonus.toFixed(0)}`}
            color={SECTION_COLORS.connectivity}
          />
        </SectionCard>
      </Box>

      {/* Trust Safety */}
      <TrustSafetySection
        pollutionPenalty={pollutionPenalty}
        structuralPenalty={structuralPenalty}
        hubSkew={hubSkew}
        reciprocalRate={reciprocalRate}
        lowQualityEntityCount={score.low_quality_entity_count ?? 0}
        lowQualityRelationshipCount={score.low_quality_relationship_count ?? 0}
      />

      {/* Final Grade */}
      <SectionCard
        title="Final Grade"
        icon={<CalculateIcon fontSize="small" />}
        color={SECTION_COLORS.finalGrade}
        tooltip={<FinalGradeTooltip />}
      >
        <FinalGradeVisual
          relationshipQuality={score.avg_relationship_quality}
          entityQuality={score.avg_entity_quality}
          topologyScore={score.topology_score ?? 0}
          pollutionPenalty={pollutionPenalty}
          structuralPenalty={structuralPenalty}
          finalGrade={score.quality_grade}
          gradeLabel={score.quality_label}
          gradeColor={gradeColor}
        />
      </SectionCard>

      {/* Refresh button */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-end",
          mt: 2
        }}>
        <Tooltip title="Recalculate score (bypass cache)">
          <IconButton aria-label="Recalculate score (bypass cache)" size="small" onClick={onRecalculate} disabled={loading}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
    </Box>
  );
}
