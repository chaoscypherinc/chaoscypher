// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Overview tab — data-product summary + a foldable "how it ran" view.
 *
 * Carries the four headline tiles (Entities / Relationships / Templates /
 * Quality), the collapsible Pipeline Flow section (promoted stat cards →
 * full funnel), the Knowledge map, and the two distribution donuts. The
 * Quality tile opens the QualityBreakdownDialog directly.
 */

import { useState } from 'react';
import { Box } from '@mui/material';
import { QualityColors } from '../../../../theme/colors';
import type { Source, SourceStats } from '../../../../types';
import { isSourceCommitted } from '../../../../types';
import { DEFAULT_NODE_ICON, DEFAULT_EDGE_ICON } from '../../../../utils/iconSprites';
import { getGradeColor } from '../../../../components/Quality/utils';
import { useQualityScore } from '../pipeline/hooks/useQualityScore';
import { QualityBreakdownDialog } from '../pipeline/QualityBreakdownDialog';
import { useOverviewData } from './hooks/useOverviewData';
import { StatsTilesRow } from './tiles/StatsTilesRow';
import { SourceGraphPreview } from './SourceGraphPreview';
import { DistributionChart } from './sections/DistributionChart';
import { PipelineFlowSection } from './sections/PipelineFlowSection';

interface OverviewTabProps {
  source: Source;
  stats: SourceStats | null;
  onNavigateToExtraction?: () => void;
}

export function OverviewTab({ source, stats, onNavigateToExtraction }: OverviewTabProps) {
  const hasLLMData =
    source.llm_total_calls > 0 ||
    source.extraction_entities_count > 0 ||
    source.extraction_relationships_count > 0;

  const { typeToTemplate } = useOverviewData(source.id);
  const { qualityScore, qualityLoading, recalculateQuality } = useQualityScore(source.id, hasLLMData);
  const [qualityOpen, setQualityOpen] = useState(false);

  const hasScore =
    !!qualityScore && (qualityScore.entity_count > 0 || qualityScore.relationship_count > 0);
  const gradeColor = hasScore ? getGradeColor(qualityScore.quality_label) : QualityColors.defaultGray;

  // Filtered counts feed the Quality tile's tooltip; live per-stage accounting
  // is in the Pipeline Flow funnel's stage board, so these stay stubbed here.
  const entitiesFiltered = 0;
  const relsFiltered = 0;
  const totalInvalid = 0;
  const totalFiltered = 0;

  const showMap = isSourceCommitted(source) && (source.extraction_entities_count ?? 0) > 0;
  const showEntityDist = !!stats && stats.entity_count > 0;
  const showRelDist = !!stats && stats.relationship_count > 0;
  const showDists = showEntityDist || showRelDist;

  const entityChart = showEntityDist ? (
    <DistributionChart
      title="Entity Distribution"
      distribution={stats?.entity_type_distribution || {}}
      typeToTemplate={typeToTemplate}
      defaultIcon={DEFAULT_NODE_ICON}
    />
  ) : null;
  const relChart = showRelDist ? (
    <DistributionChart
      title="Relationship Distribution"
      distribution={stats?.relationship_type_distribution || {}}
      typeToTemplate={typeToTemplate}
      defaultIcon={DEFAULT_EDGE_ICON}
    />
  ) : null;

  return (
    <Box>
      <StatsTilesRow
        source={source}
        entitiesFiltered={entitiesFiltered}
        relsFiltered={relsFiltered}
        totalInvalid={totalInvalid}
        totalFiltered={totalFiltered}
        qualityLoading={qualityLoading}
        qualityScore={qualityScore}
        hasScore={!!hasScore}
        gradeColor={gradeColor}
        onOpenQuality={() => setQualityOpen(true)}
        onNavigateToExtraction={onNavigateToExtraction}
      />

      <PipelineFlowSection source={source} stats={stats} />

      {showMap && showDists ? (
        <Box
          sx={{
            mb: 3,
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', md: '3fr 2fr' },
            gap: 1.5,
            alignItems: 'stretch',
          }}
        >
          <SourceGraphPreview source={source} />
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, minWidth: 0 }}>
            {entityChart && <Box sx={{ minWidth: 0 }}>{entityChart}</Box>}
            {relChart && <Box sx={{ minWidth: 0 }}>{relChart}</Box>}
          </Box>
        </Box>
      ) : (
        <>
          {showMap && (
            <Box sx={{ mb: 3 }}>
              <SourceGraphPreview source={source} />
            </Box>
          )}
          {showDists && (
            <Box sx={{ mb: 3, display: 'flex', gap: 1.5 }}>
              {entityChart}
              {relChart}
            </Box>
          )}
        </>
      )}

      <QualityBreakdownDialog
        open={qualityOpen}
        score={qualityScore}
        loading={qualityLoading}
        onClose={() => setQualityOpen(false)}
        onRecalculate={recalculateQuality}
      />
    </Box>
  );
}
