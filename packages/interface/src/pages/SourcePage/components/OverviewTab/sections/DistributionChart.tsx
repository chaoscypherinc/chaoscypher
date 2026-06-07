// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import React, { useState } from 'react';
import { Box, IconButton, Typography } from '@mui/material';
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ChartTooltip, CategoricalPalette } from '../../../../../theme/charts';
import { surfaceSx } from '../../../../../theme/cardStyles';
import { getColorForTemplate } from '../../../../../utils/colorUtils';
import { getMuiIcon } from '../../../../../utils/icons';
import { cleanTypeName } from '../formatters';
import { deduplicateColors } from '../chartColors';
import type { TemplateInfo } from '../hooks/useOverviewData';

interface DistributionChartProps {
  title: string;
  distribution: Record<string, number>;
  typeToTemplate: Map<string, TemplateInfo>;
  defaultIcon: string;
}

interface ChartTickInfo {
  icon: string;
  color: string;
}

interface ChartTickProps {
  x?: number | string;
  y?: number | string;
  payload?: { value: string };
  iconLookup: Map<string, ChartTickInfo>;
}

function ChartTick({ x, y, payload, iconLookup }: ChartTickProps) {
  const info = iconLookup.get(payload?.value ?? '');
  const iconColor = info?.color || ChartTooltip.tick;
  const iconElement = info
    ? React.createElement(getMuiIcon(info.icon), {
        style: { fontSize: 16, color: iconColor },
      })
    : null;
  return (
    <g transform={`translate(${x},${y})`}>
      <text x={-28} y={0} dy={4} textAnchor="end" fill={ChartTooltip.tick} fontSize={12}>
        {payload?.value ?? ''}
      </text>
      {iconElement && (
        <foreignObject x={-22} y={-10} width={20} height={20}>
          {iconElement}
        </foreignObject>
      )}
    </g>
  );
}

/** Rows shown per page. Fixed so both distribution charts are the same height. */
const PAGE_SIZE = 7;
const ROW_HEIGHT = 36;

/**
 * Generic horizontal bar chart for entity-type or relationship-type
 * distributions. Pulls icon + color from `typeToTemplate` when the
 * type maps to a known template, falling back to the categorical
 * palette and `defaultIcon` otherwise.
 *
 * Items beyond one page are reached via the prev/next pager at the bottom; the
 * chart body is a fixed height (``PAGE_SIZE`` rows) so two charts stacked
 * beside the Knowledge map line up at equal sizes.
 */
export function DistributionChart({
  title,
  distribution,
  typeToTemplate,
  defaultIcon,
}: DistributionChartProps) {
  const [activeBarIndex, setActiveBarIndex] = useState<number | null>(null);
  const [page, setPage] = useState(0);

  const allChartData = Object.entries(distribution)
    .map(([type, count]) => {
      const cleaned = cleanTypeName(type);
      const tpl = typeToTemplate.get(cleaned.toLowerCase());
      const tplColor = tpl?.color || (tpl ? getColorForTemplate(tpl.id) : null);
      return {
        name: cleaned,
        count: count as number,
        templateColor: tplColor,
        templateIcon: tpl?.icon || defaultIcon,
      };
    })
    .sort((a, b) => b.count - a.count);

  const pageCount = Math.max(1, Math.ceil(allChartData.length / PAGE_SIZE));
  // Clamp during render so a shrinking dataset can't strand us past the end.
  const currentPage = Math.min(page, pageCount - 1);
  const start = currentPage * PAGE_SIZE;
  const chartData = allChartData.slice(start, start + PAGE_SIZE);
  const dedupedColors = deduplicateColors(chartData, CategoricalPalette);

  const iconLookup = new Map<string, ChartTickInfo>(
    chartData.map((d, i) => [d.name, { icon: d.templateIcon, color: dedupedColors[i] }]),
  );

  return (
    <Box sx={{ flex: 1, p: 2, ...surfaceSx }}>
      <Typography variant="subtitle2" sx={{ color: 'text.secondary', mb: 1 }}>
        {title}
      </Typography>
      <ResponsiveContainer width="100%" height={PAGE_SIZE * ROW_HEIGHT + 20}>
        <BarChart data={chartData} layout="vertical" margin={{ left: 20, right: 40 }}>
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="name"
            tick={(props) => <ChartTick {...props} iconLookup={iconLookup} />}
            width={140}
            axisLine={false}
            tickLine={false}
          />
          <RechartsTooltip
            cursor={{ fill: ChartTooltip.cursor }}
            contentStyle={{
              backgroundColor: ChartTooltip.background,
              border: `1px solid ${ChartTooltip.border}`,
              borderRadius: '4px',
            }}
            itemStyle={{ color: ChartTooltip.text }}
            labelStyle={{ color: ChartTooltip.text }}
            formatter={(value) => [String(value), 'Count']}
          />
          <Bar
            dataKey="count"
            radius={[0, 4, 4, 0]}
            barSize={24}
            onMouseEnter={(_, index) => setActiveBarIndex(index)}
            onMouseLeave={() => setActiveBarIndex(null)}
          >
            {chartData.map((_entry, index) => (
              <Cell
                key={`cell-${index}`}
                fill={dedupedColors[index]}
                fillOpacity={activeBarIndex === index ? 0.7 : 1}
                style={{ cursor: 'pointer', transition: 'fill-opacity 0.2s ease' }}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      {/* Fixed-height footer (pager when multi-page, count otherwise) so both
          charts stay the same overall size. */}
      <Box
        sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1, mt: 1, minHeight: 32 }}
      >
        {pageCount > 1 ? (
          <>
            <IconButton
              size="small"
              aria-label="Previous page"
              disabled={currentPage === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              <ChevronLeftIcon fontSize="small" />
            </IconButton>
            <Typography variant="caption" sx={{ color: 'text.secondary', minWidth: 92, textAlign: 'center' }}>
              {`Page ${currentPage + 1} of ${pageCount}`}
            </Typography>
            <IconButton
              size="small"
              aria-label="Next page"
              disabled={currentPage >= pageCount - 1}
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            >
              <ChevronRightIcon fontSize="small" />
            </IconButton>
          </>
        ) : (
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {`${allChartData.length} ${allChartData.length === 1 ? 'type' : 'types'}`}
          </Typography>
        )}
      </Box>
    </Box>
  );
}
