// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography } from '@mui/material';
import { glassPanelSx } from '../../../../theme/cardStyles';

interface StatsCardProps {
  /** Distinct neighbour count from the connections endpoint. */
  connectionsTotal: number;
  /** Total incident edges (incoming + outgoing); may exceed connectionsTotal when the same neighbour has multiple typed edges. */
  edgesTotal?: number | null;
  citationsTotal: number;
  propertiesCount: number;
}

interface StatRowProps {
  label: string;
  value: number | string;
  divider?: boolean;
}

function StatRow({ label, value, divider = true }: StatRowProps) {
  return (
    <Box
      sx={{
        py: 1.5,
        borderBottom: divider ? '1px solid rgba(255, 255, 255, 0.06)' : undefined,
        display: 'flex',
        justifyContent: 'space-between',
      }}
    >
      <Typography variant="body2" sx={{ color: 'text.secondary' }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ fontWeight: 'medium' }}>
        {value}
      </Typography>
    </Box>
  );
}

/**
 * Sidebar statistics card for NodeDetailPage. Renders read-only stat rows:
 * connected entities (distinct neighbours), total edges (when available),
 * source citations, and properties count.
 */
export default function StatsCard({
  connectionsTotal,
  edgesTotal,
  citationsTotal,
  propertiesCount,
}: StatsCardProps) {
  const showEdges = typeof edgesTotal === 'number' && edgesTotal !== connectionsTotal;
  return (
    <Box sx={{ ...glassPanelSx, p: 2.5, mt: 2 }}>
      <Typography variant="h6" gutterBottom sx={{ color: 'text.primary' }}>
        Statistics
      </Typography>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
        <StatRow label="Connections" value={connectionsTotal} />
        {showEdges && <StatRow label="Edges" value={edgesTotal as number} />}
        <StatRow label="Source Citations" value={citationsTotal} />
        <StatRow label="Properties" value={propertiesCount} />
      </Box>
    </Box>
  );
}
