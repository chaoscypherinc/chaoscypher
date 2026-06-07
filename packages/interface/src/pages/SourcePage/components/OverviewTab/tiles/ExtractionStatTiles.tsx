// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import EntitiesIcon from '@mui/icons-material/AccountTree';
import TemplatesIcon from '@mui/icons-material/Category';
import RelationsIcon from '@mui/icons-material/Link';
import { ContentTypeColors } from '../../../../../theme/colors';
import type { Source } from '../../../../../types';
import { StatTile } from '../../../../../components/sources/StatTile';

interface ExtractionStatTilesProps {
  source: Source;
  onNavigateToExtraction?: () => void;
}

/**
 * Renders the Entities / Relationships / Templates stat tiles. Each tile
 * shows its headline count with a one-line plain-English tooltip describing
 * what the number represents. The raw/filtered/dedup and commit-side
 * (Graph Nodes/Edges) breakdowns were removed 2026-05-26 — their data was
 * never wired on this tab (rendered as dead rows), and the divergent
 * Final-vs-Graph counts read as data loss rather than the cross-source
 * dedup / inverse-edge behaviour they actually reflect.
 */
export function ExtractionStatTiles({ source, onNavigateToExtraction }: ExtractionStatTilesProps) {
  return (
    <>
      <StatTile
        value={source.extraction_entities_count || 0}
        label="Entities"
        color={ContentTypeColors.entities}
        icon={<EntitiesIcon fontSize="inherit" />}
        onClick={onNavigateToExtraction}
        ariaLabel={onNavigateToExtraction ? `Entities: ${source.extraction_entities_count || 0}, open Extraction tab` : undefined}
        tooltip="Distinct entities extracted from this source."
      />

      <StatTile
        value={source.extraction_relationships_count || 0}
        label="Relationships"
        color={ContentTypeColors.relationships}
        icon={<RelationsIcon sx={{ fontSize: 'inherit' }} />}
        onClick={onNavigateToExtraction}
        ariaLabel={onNavigateToExtraction ? `Relationships: ${source.extraction_relationships_count || 0}, open Extraction tab` : undefined}
        tooltip="Relationships extracted from this source."
      />

      <StatTile
        value={source.commit_templates_created || 0}
        label="Templates"
        color={ContentTypeColors.templates}
        icon={<TemplatesIcon fontSize="inherit" />}
        onClick={onNavigateToExtraction}
        ariaLabel={onNavigateToExtraction ? `Templates: ${source.commit_templates_created || 0}, open Extraction tab` : undefined}
        tooltip="Unique entity & relationship type schemas created."
      />
    </>
  );
}
