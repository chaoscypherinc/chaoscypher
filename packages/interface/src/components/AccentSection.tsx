// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * AccentSection — themed wrapper Box with colored bottom border + tint.
 *
 * Style helpers (accentSx, accentSelectSx, accentPaperSx, ACCENT_COLORS) live
 * in ./accentStyles so this file is Fast-Refresh-clean.
 */
import { Box, type SxProps, type Theme } from '@mui/material';
import type { ReactNode } from 'react';
import { accentSx } from '../theme/accentStyles';

interface AccentSectionProps {
  /** Named color preset or hex color. Omit for neutral treatment. */
  color?: string;
  /** Content to render inside the section. */
  children: ReactNode;
  /** Additional sx props merged on top of the accent treatment. */
  sx?: SxProps<Theme>;
}

/**
 * Themed section wrapper with colored bottom border and tinted background.
 */
export function AccentSection({ color, children, sx }: AccentSectionProps) {
  return (
    <Box sx={{ ...accentSx(color), ...(sx as object) }}>
      {children}
    </Box>
  );
}
