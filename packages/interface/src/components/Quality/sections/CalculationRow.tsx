// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * CalculationRow - Formula display row for quality calculations.
 *
 * Displays a labeled calculation with optional formula breakdown and result.
 * Supports warning styling for penalty displays.
 */

import { Box, Typography } from '@mui/material';

interface CalculationRowProps {
  /** Description label (e.g., "Contributes") */
  label: string;
  /** Optional formula string (e.g., "75.2 × 0.6") */
  formula?: string;
  /** Result value (e.g., "= 45.1 points") */
  result: string;
  /** Optional custom color for the result */
  color?: string;
  /** Whether this is a warning/penalty row */
  warning?: boolean;
}

/**
 * A row displaying a calculation formula with label and result.
 *
 * Used to show how quality scores are calculated with clear formula
 * breakdowns and visual distinction for penalties.
 */
export function CalculationRow({
  label,
  formula,
  result,
  color,
  warning = false,
}: CalculationRowProps) {
  const textColor = warning ? 'warning.main' : color || 'text.secondary';

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        py: 0.25,
      }}
    >
      <Typography variant="caption" sx={{
        color: "text.secondary"
      }}>
        {label}
        {formula && (
          <Typography
            component="span"
            variant="caption"
            sx={{ ml: 0.5, fontFamily: 'monospace', color: 'text.primary' }}
          >
            {formula}
          </Typography>
        )}
      </Typography>
      <Typography
        variant="caption"
        sx={{
          fontWeight: 600,
          color: textColor,
          fontFamily: 'monospace'
        }}>
        {result}
      </Typography>
    </Box>
  );
}
