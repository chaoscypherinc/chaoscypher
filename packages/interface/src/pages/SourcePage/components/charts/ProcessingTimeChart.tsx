// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Processing Time Distribution Chart.
 *
 * Scatter chart showing LLM processing duration per chunk group.
 * Orange dots indicate groups that required retries.
 */

import { Typography, Box, useTheme } from '@mui/material';
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import type { ExtractionChartTask, ExtractionTaskStats } from '../../../../types';

interface ProcessingTimeChartProps {
  chartTasks: ExtractionChartTask[];
  stats?: ExtractionTaskStats | null;
  height?: number;
}

export function ProcessingTimeChart({ chartTasks, stats: _stats, height = 250 }: ProcessingTimeChartProps) {
  const theme = useTheme();

  // Filter tasks with timing data (uses ALL tasks from chartTasks)
  const data = chartTasks
    .filter((t) => t.llm_duration_ms != null && t.llm_duration_ms > 0)
    .map((t) => ({
      group: t.chunk_index + 1,
      duration: (t.llm_duration_ms ?? 0) / 1000,
      status: t.status,
      retries: t.retry_count,
    }));

  if (data.length === 0) {
    return (
      <Box sx={{ height: 140, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Typography sx={{ color: 'text.secondary', fontSize: '0.75rem' }}>
          No timing data available yet
        </Typography>
      </Box>
    );
  }

  // Calculate statistics for reference lines
  const durations = data.map((d) => d.duration);
  const avg = durations.reduce((a, b) => a + b, 0) / durations.length;
  const sorted = [...durations].sort((a, b) => a - b);
  const p95 = sorted[Math.floor(sorted.length * 0.95)] || avg;

  return (
    <Box sx={{ height }}>
      <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={0}>
        <ScatterChart margin={{ top: 10, right: 10, bottom: 25, left: 50 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
          <XAxis
            dataKey="group"
            name="Group"
            type="number"
            domain={['dataMin', 'dataMax']}
            tick={{ fontSize: 10 }}
            tickFormatter={(value) => String(Math.round(value))}
            label={{ value: 'Group', position: 'bottom', offset: 5, fontSize: 10, fill: theme.palette.text.secondary }}
          />
          <YAxis
            dataKey="duration"
            name="Duration"
            unit="s"
            tick={{ fontSize: 10 }}
            width={50}
            label={{ value: 'Duration (s)', angle: -90, position: 'insideLeft', dx: -5, fontSize: 10, fill: theme.palette.text.secondary }}
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
              // Only format duration, not group number
              if (name === 'Duration') return [`${numeric.toFixed(2)}s`, 'Duration'];
              return [numeric, String(name)];
            }}
            labelFormatter={(label) => `Group ${label}`}
          />
          <ReferenceLine y={avg} stroke={theme.palette.success.main} strokeDasharray="3 3" />
          <ReferenceLine y={p95} stroke={theme.palette.warning.main} strokeDasharray="3 3" />
          <Scatter
            data={data}
            fill={theme.palette.primary.main}
            shape={(props) => {
              const { cx, cy, payload } = props as {
                cx?: number;
                cy?: number;
                payload?: { retries?: number };
              };
              const color = (payload?.retries ?? 0) > 0
                ? theme.palette.warning.main
                : theme.palette.primary.main;
              return <circle cx={cx} cy={cy} r={4} fill={color} />;
            }}
          />
        </ScatterChart>
      </ResponsiveContainer>
    </Box>
  );
}
