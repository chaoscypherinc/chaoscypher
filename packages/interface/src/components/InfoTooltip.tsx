// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Generic metadata tooltip for entity rows.
 *
 * Renders a compact list of label-value pairs inside a MUI Tooltip.
 * Callers build the items array, filtering out conditional entries.
 */

import { Box, Typography } from '@mui/material';
import type { SxProps, Theme } from '@mui/material';

interface InfoTooltipItem {
  label: string;
  value: React.ReactNode;
  sx?: SxProps<Theme>;
}

export function InfoTooltip({ items }: { items: InfoTooltipItem[] }) {
  return (
    <Box sx={{ p: 0.5 }}>
      {items.map((item, i) => (
        <Typography
          key={i}
          variant="caption"
          sx={{ display: 'block', ...item.sx }}
        >
          {item.label}: {item.value}
        </Typography>
      ))}
    </Box>
  );
}
