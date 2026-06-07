// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * StatsTilesRow renders the Overview tab's tile row.
 *
 * Slimmed 2026-05-11 — the three "how the pipeline ran" tiles
 * (Groups / Avg / Tokens) moved to the Pipeline Flow section scoreboard;
 * Overview now keeps only the four data-product tiles plus the small
 * Quality tile (clickable, opens the QualityBreakdownDialog in place).
 */

import { Box } from '@mui/material';
import type { Source, SourceQualityScore } from '../../../../../types';
import { ExtractionStatTiles } from './ExtractionStatTiles';
import { QualityStatTile } from './QualityStatTile';

interface StatsTilesRowProps {
  source: Source;
  entitiesFiltered: number;
  relsFiltered: number;
  totalInvalid: number;
  totalFiltered: number;
  qualityLoading: boolean;
  qualityScore: SourceQualityScore | null;
  hasScore: boolean;
  gradeColor: string;
  /** Opens the QualityBreakdownDialog (owned by OverviewTab). */
  onOpenQuality?: () => void;
  /** Called when an Extraction stat tile (Entities/Relationships/Templates) is clicked. */
  onNavigateToExtraction?: () => void;
}

export function StatsTilesRow({
  source,
  entitiesFiltered,
  relsFiltered,
  totalInvalid,
  totalFiltered,
  qualityLoading,
  qualityScore,
  hasScore,
  gradeColor,
  onOpenQuality,
  onNavigateToExtraction,
}: StatsTilesRowProps) {
  if (source.chunk_count === 0) return null;

  return (
    <Box sx={{ mb: 3, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
      <ExtractionStatTiles
        source={source}
        onNavigateToExtraction={onNavigateToExtraction}
      />
      <QualityStatTile
        qualityLoading={qualityLoading}
        qualityScore={qualityScore}
        hasScore={hasScore}
        gradeColor={gradeColor}
        totalFiltered={totalFiltered}
        entitiesFiltered={entitiesFiltered}
        relsFiltered={relsFiltered}
        totalInvalid={totalInvalid}
        isPypdf={source.indexing_extraction_method === 'pypdf'}
        onClick={onOpenQuality}
      />
    </Box>
  );
}
