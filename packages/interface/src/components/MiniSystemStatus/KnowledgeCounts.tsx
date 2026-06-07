// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * KnowledgeCounts: Knowledge graph stats grid and automations row.
 *
 * Renders a 2x2 grid showing entities, relationships, templates, and vectors,
 * followed by an automations menu item. Used inside the MiniSystemStatus
 * dropdown.
 */

import { Box, Typography , alpha } from '@mui/material';
import { ChaosCypherPalette } from '../../theme/palette';
import type { HealthCheckResponse } from '../../types/health';
import type { KnowledgeCounts as KnowledgeCountsData } from './useSystemStatusData';

/** Grid cell configuration. */
interface GridCell {
  label: string;
  count: number;
  color: string;
  path: string;
}

/** Build the 2x2 grid cells from count data and health response. */
function buildGridCells(
  counts: KnowledgeCountsData,
  health: HealthCheckResponse | null,
): GridCell[] {
  const vectorCount =
    (health?.checks?.search_index?.details?.vector_count as number | undefined) ?? 0;

  return [
    {
      label: 'Entities',
      count: counts.knowledge_nodes,
      color: ChaosCypherPalette.primary,
      path: '/nodes',
    },
    {
      label: 'Relationships',
      count: counts.links,
      color: ChaosCypherPalette.secondary,
      path: '/edges',
    },
    {
      label: 'Templates',
      count: counts.templates,
      color: alpha('#fff', 0.45),
      path: '/templates',
    },
    {
      label: 'Vectors',
      count: vectorCount,
      color: alpha('#fff', 0.45),
      path: '/settings?tab=search',
    },
  ];
}

interface KnowledgeCountsProps {
  /** Knowledge count data (null when not yet loaded). */
  counts: KnowledgeCountsData | null;
  /** Health check response for vector count. */
  health: HealthCheckResponse | null;
  /** Callback to navigate to a route and close the menu. */
  onNavigate: (path: string) => void;
}

/**
 * Render knowledge stats as a 2x2 grid plus an automations row.
 *
 * The grid shows entities, relationships, templates, and vectors with
 * prominent count values. Automations appears as a standard menu item
 * below the grid.
 */
export function KnowledgeCounts({ counts, health, onNavigate }: KnowledgeCountsProps) {
  if (!counts) return null;

  const cells = buildGridCells(counts, health);

  return (
    <Box>
      {/* 2x2 count grid */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '1px',
          bgcolor: 'rgba(255, 255, 255, 0.06)',
          borderTop: '1px solid rgba(255, 255, 255, 0.06)',
        }}
      >
        {cells.map(cell => (
          <Box
            key={cell.path}
            onClick={() => onNavigate(cell.path)}
            sx={{
              bgcolor: 'rgba(5, 5, 10, 0.85)',
              py: 1.25,
              px: 2,
              cursor: 'pointer',
              transition: 'background 0.15s ease',
              '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.03)' },
            }}
          >
            <Typography
              sx={{
                fontFamily: '"JetBrains Mono", monospace',
                fontSize: '1rem',
                fontWeight: 700,
                letterSpacing: '-0.03em',
                color: cell.color,
                lineHeight: 1.2,
              }}
            >
              {cell.count.toLocaleString()}
            </Typography>
            <Typography
              sx={{
                fontSize: '0.6rem',
                color: 'rgba(255, 255, 255, 0.45)',
                textTransform: 'uppercase',
                letterSpacing: '0.8px',
                lineHeight: 1.4,
              }}
            >
              {cell.label}
            </Typography>
          </Box>
        ))}
      </Box>
    </Box>
  );
}
