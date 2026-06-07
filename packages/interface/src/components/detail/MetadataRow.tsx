// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography } from '@mui/material';
import type { ReactNode } from 'react';

interface MetadataRowProps {
  /** Caption label displayed above the content. */
  label: string;
  /** Content renders immediately below the label with no wrapper. */
  children: ReactNode;
  /** Render a bottom border (default: true). Set false for the last row. */
  divider?: boolean;
}

/**
 * A single row in a MetadataCard: caption label above a content block,
 * separated by a subtle bottom border.
 */
export default function MetadataRow({ label, children, divider = true }: MetadataRowProps) {
  return (
    <Box
      sx={{
        py: 1.5,
        borderBottom: divider ? '1px solid rgba(255, 255, 255, 0.06)' : undefined,
      }}
    >
      <Typography variant="caption" sx={{ color: 'text.secondary' }}>
        {label}
      </Typography>
      {children}
    </Box>
  );
}
