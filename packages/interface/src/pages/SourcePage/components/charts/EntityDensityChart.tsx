// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Extraction Results Chart - Stacked bar showing entities and relationships per group.
 *
 * Shows entities and relationships stacked for each chunk group.
 * Invalid relationships shown in tooltip.
 */

import { Typography, Box, useTheme } from '@mui/material';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import type { ExtractionChartTask, ExtractionTaskStats } from '../../../../types';
import { ChaosCypherPalette } from '../../../../theme/palette';

interface EntityDensityChartProps {
  chartTasks: ExtractionChartTask[];
  stats?: ExtractionTaskStats | null;
  height?: number;
}

export function EntityDensityChart({ chartTasks, stats: _stats, height = 250 }: EntityDensityChartProps) {
  const theme = useTheme();

  // Transform data for stacked bar chart
  // Stack order (bottom to top): Invalid → Entities → Relationships
  const data = chartTasks.map((t) => ({
    group: t.chunk_index + 1,
    invalid: t.invalid_relationship_count || 0,
    entities: t.entity_count,
    relationships: t.relationship_count,
  }));

  if (data.length === 0) {
    return (
      <Box sx={{ height: 140, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Typography sx={{ color: 'text.secondary', fontSize: '0.75rem' }}>
          No extraction data available yet
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ height }}>
      <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
        <BarChart data={data} margin={{ top: 20, right: 10, bottom: 25, left: 50 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
          <XAxis
            dataKey="group"
            tick={{ fontSize: 10 }}
            label={{ value: 'Group', position: 'bottom', offset: 5, fontSize: 10, fill: theme.palette.text.secondary }}
          />
          <YAxis
            tick={{ fontSize: 10 }}
            width={50}
            label={{ value: 'Count', angle: -90, position: 'insideLeft', dx: -5, fontSize: 10, fill: theme.palette.text.secondary }}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: theme.palette.background.paper,
              border: `1px solid ${theme.palette.divider}`,
              borderRadius: 4,
              fontSize: 12,
            }}
            itemStyle={{
              color: theme.palette.text.primary,
            }}
            labelStyle={{
              color: theme.palette.text.primary,
              fontWeight: 500,
            }}
            formatter={(value, name) => {
              const numeric = typeof value === 'number' ? value : Number(value);
              const key = String(name);
              if (key === 'invalid') return [numeric, 'Invalid'];
              if (key === 'entities') return [numeric, 'Entities'];
              if (key === 'relationships') return [numeric, 'Relationships'];
              return [numeric, key];
            }}
            labelFormatter={(label) => `Group ${label}`}
          />
          <Legend
            verticalAlign="top"
            height={20}
            wrapperStyle={{ fontSize: 10, paddingBottom: 5 }}
            iconSize={10}
            formatter={(value) => {
              if (value === 'entities') return 'Entities';
              if (value === 'relationships') return 'Relationships';
              if (value === 'invalid') return 'Invalid';
              return value;
            }}
          />
          {/* Stack order: Invalid (bottom) → Entities → Relationships (top) */}
          <Bar
            dataKey="invalid"
            stackId="a"
            fill={theme.palette.error.main}
            radius={[0, 0, 0, 0]}
          />
          <Bar
            dataKey="entities"
            stackId="a"
            fill={theme.palette.primary.main}
            radius={[0, 0, 0, 0]}
          />
          <Bar
            dataKey="relationships"
            stackId="a"
            fill={ChaosCypherPalette.accent}
            radius={[2, 2, 0, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </Box>
  );
}
