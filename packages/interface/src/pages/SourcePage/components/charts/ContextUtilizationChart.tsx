// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Context Utilization — quiet range meter.
 *
 * The whole bar is the context window. At rest it shows just the muted
 * min–max usage band, an avg tick, the window size at the right end, and one
 * number: the average % of the window each extraction call used. On hover the
 * low / mean / high readings appear right on the line at their positions —
 * the token count above the bar, the % below it — instead of a block of text,
 * so it stays clean and it's obvious what's what.
 *
 * Severity colour (green / amber / red) tracks the average utilisation; an
 * "Over budget" badge appears only when peak usage exceeded the window.
 */

import { Typography, Box, Chip, Tooltip, useTheme, alpha } from '@mui/material';
import WarningIcon from '@mui/icons-material/Warning';
import type { ExtractionTask, ExtractionTaskStats } from '../../../../types';
import { useContextUtilization, getUtilizationColor, formatNumber } from './useContextUtilization';

interface ContextUtilizationChartProps {
  tasks: ExtractionTask[];
  stats?: ExtractionTaskStats | null;
}

/** Compact token formatter for the on-line labels (1,240 → "1.2k"). */
function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return String(n);
}

/** Keep an on-line label inside the track at the edges, centered otherwise. */
function labelTransform(pos: number): string {
  if (pos < 12) return 'translateX(0)';
  if (pos > 88) return 'translateX(-100%)';
  return 'translateX(-50%)';
}

export function ContextUtilizationChart({ tasks, stats }: ContextUtilizationChartProps) {
  const theme = useTheme();
  const { actualStats, contextWindow, linePositions, overBudget } = useContextUtilization(tasks, stats);

  if (!actualStats) {
    return (
      <Box sx={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Typography sx={{ color: 'text.secondary', fontSize: '0.75rem' }}>
          No token data available yet. Re-extract to capture token metrics.
        </Typography>
      </Box>
    );
  }

  const severity = getUtilizationColor(actualStats.avgUtilization);
  const severityColor = theme.palette[severity].main;
  const avgPct = actualStats.avgUtilization.toFixed(0);
  const rangeWidth = Math.max(0, linePositions.max - linePositions.min);

  // low / mean / high readings placed on the line: token above, % below.
  const markers = [
    { key: 'low', pos: linePositions.min, pct: actualStats.minUtilization, tokens: actualStats.minUsed },
    { key: 'mean', pos: linePositions.avg, pct: actualStats.avgUtilization, tokens: actualStats.avgUsed, accent: true },
    { key: 'high', pos: linePositions.max, pct: actualStats.maxUtilization, tokens: actualStats.maxUsed },
  ];

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      {/* Hover region: the bar plus its reveal-on-hover on-line labels. */}
      <Box sx={{ flex: 1, position: 'relative', py: 2.25, '&:hover .ctx-mk': { opacity: 1 } }}>
        {/* Window-size anchor — the bar's right edge is the full window. */}
        <Typography
          sx={{ position: 'absolute', top: 2, right: 0, fontSize: '0.58rem', color: 'text.disabled' }}
        >
          {fmtTokens(contextWindow)} window
        </Typography>

        <Box
          sx={{
            position: 'relative',
            height: 10,
            borderRadius: 5,
            bgcolor: 'rgba(255,255,255,0.05)',
            border: '1px solid',
            borderColor: 'divider',
            overflow: 'visible',
          }}
        >
          {/* min–max usage band */}
          <Box
            sx={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: `${linePositions.min}%`,
              width: `${rangeWidth}%`,
              borderRadius: 5,
              bgcolor: alpha(severityColor, 0.35),
            }}
          />
          {/* avg tick */}
          <Box
            sx={{
              position: 'absolute',
              top: -2,
              bottom: -2,
              left: `${linePositions.avg}%`,
              width: 2,
              bgcolor: severityColor,
            }}
          />

          {/* On-line labels: token above the line, % below. */}
          {markers.map((m) => (
            <Box key={m.key}>
              <Typography
                className="ctx-mk"
                sx={{
                  position: 'absolute',
                  bottom: 'calc(100% + 5px)',
                  left: `${m.pos}%`,
                  transform: labelTransform(m.pos),
                  opacity: 0,
                  transition: 'opacity 0.15s',
                  whiteSpace: 'nowrap',
                  pointerEvents: 'none',
                  fontSize: '0.65rem',
                  fontWeight: 700,
                  lineHeight: 1,
                  color: m.accent ? severityColor : 'text.secondary',
                }}
              >
                {fmtTokens(m.tokens)}
              </Typography>
              <Typography
                className="ctx-mk"
                sx={{
                  position: 'absolute',
                  top: 'calc(100% + 5px)',
                  left: `${m.pos}%`,
                  transform: labelTransform(m.pos),
                  opacity: 0,
                  transition: 'opacity 0.15s',
                  whiteSpace: 'nowrap',
                  pointerEvents: 'none',
                  fontSize: '0.58rem',
                  lineHeight: 1,
                  color: 'text.disabled',
                }}
              >
                {m.pct.toFixed(0)}%
              </Typography>
            </Box>
          ))}
        </Box>
      </Box>

      <Typography sx={{ fontSize: '0.85rem', fontWeight: 700, lineHeight: 1, color: severityColor }}>
        {avgPct}%
      </Typography>
      {overBudget && (
        <Tooltip
          title={`Peak usage exceeded the configured context window (${formatNumber(contextWindow)} tokens). The bar is clamped to 100%.`}
          arrow
        >
          <Chip
            size="small"
            icon={<WarningIcon sx={{ fontSize: 12 }} />}
            label="Over budget"
            color="error"
            variant="outlined"
            sx={{ height: 18, '& .MuiChip-label': { px: 0.75, fontSize: '0.6rem' } }}
          />
        </Tooltip>
      )}
    </Box>
  );
}
