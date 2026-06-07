// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * MetricProgress - Visual progress bar with labels for quality metrics.
 *
 * Displays a linear progress bar with optional label and value display.
 * Supports custom colors and percentage/value display modes.
 */

import { Box, Typography, LinearProgress, alpha } from '@mui/material';

interface MetricProgressProps {
  /** Current value */
  value: number;
  /** Maximum value (default 100) */
  max?: number;
  /** Label displayed above the progress bar */
  label?: string;
  /** Suffix for the value (e.g., "%", "/100") */
  suffix?: string;
  /** Whether to show percentage in the bar */
  showPercentage?: boolean;
  /** Custom color for the progress bar */
  color?: string;
}

/**
 * A progress bar with optional labels for displaying quality metrics.
 *
 * Used to visualize scores on a 0-100 scale with clear visual feedback
 * on the current value relative to the maximum.
 */
export function MetricProgress({
  value,
  max = 100,
  label,
  suffix = '',
  showPercentage = true,
  color,
}: MetricProgressProps) {
  const percentage = Math.min(100, (value / max) * 100);
  const displayValue = typeof value === 'number' && value % 1 !== 0
    ? value.toFixed(1)
    : value.toString();

  return (
    <Box sx={{ mb: 1 }}>
      {/* Label row */}
      {label && (
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            mb: 0.5,
          }}
        >
          <Typography variant="caption" sx={{
            color: "text.secondary"
          }}>
            {label}
          </Typography>
          <Typography variant="caption" sx={{
            fontWeight: 600
          }}>
            {displayValue}{suffix}
          </Typography>
        </Box>
      )}
      {/* Progress bar container */}
      <Box sx={{ position: 'relative' }}>
        <LinearProgress
          variant="determinate"
          value={percentage}
          sx={{
            height: 8,
            borderRadius: 1,
            bgcolor: (theme) => alpha(color || theme.palette.primary.main, 0.15),
            '& .MuiLinearProgress-bar': {
              borderRadius: 1,
              bgcolor: color,
              transition: 'transform 0.4s ease',
            },
          }}
        />
        {/* Percentage label inside bar (when bar is wide enough) */}
        {showPercentage && percentage > 15 && (
          <Typography
            variant="caption"
            sx={{
              position: 'absolute',
              left: `${Math.min(percentage - 2, 95)}%`,
              top: '50%',
              transform: 'translate(-100%, -50%)',
              color: 'white',
              fontSize: '0.6rem',
              fontWeight: 600,
              textShadow: '0 1px 2px rgba(0,0,0,0.3)',
            }}
          >
            {Math.round(percentage)}%
          </Typography>
        )}
      </Box>
    </Box>
  );
}
