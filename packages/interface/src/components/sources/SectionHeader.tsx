// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography } from '@mui/material';
import type { ReactNode } from 'react';

interface SectionHeaderProps {
  label: string;
  icon?: ReactNode;
}

/**
 * Uppercase overline + optional icon. Single section-title style reused
 * across every Source-detail tab so sections read consistently.
 */
export function SectionHeader({ label, icon }: SectionHeaderProps) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mt: 2, mb: 1 }}>
      {icon && <Box sx={{ display: 'flex', fontSize: 14, color: 'text.secondary' }}>{icon}</Box>}
      <Typography
        sx={{
          fontSize: '0.6rem',
          letterSpacing: 1.2,
          textTransform: 'uppercase',
          color: 'text.secondary',
          opacity: 0.7,
        }}
      >
        {label}
      </Typography>
    </Box>
  );
}
