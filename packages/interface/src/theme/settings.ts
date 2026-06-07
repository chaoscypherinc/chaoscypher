// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared styles for Settings page components.
 *
 * Centralizes accordion, button, and summary styles used across
 * General, Search, Maintenance, and other settings tabs.
 */

import { alpha } from '@mui/material';
import type { SxProps, Theme } from '@mui/material';
import { ACCENT_COLORS, createAccentOverrides } from './accentStyles';

/** Accordion with colored left accent on header, faint on content. */
export function accentAccordionSx(color: string): SxProps<Theme> {
  const hex = ACCENT_COLORS[color] || color;
  return {
    '&:before': { display: 'none' },
    borderRadius: 0,
    borderBottom: `1px solid rgba(255, 255, 255, 0.06)`,
    transition: 'all 0.2s ease-in-out',
    // Header gets the strong accent line
    '& > .MuiAccordionSummary-root': {
      borderLeft: `2px solid ${alpha(hex, 0.5)}`,
      '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.03)' },
    },
    // Expanded content gets a faint version
    '& > .MuiCollapse-root .MuiAccordionDetails-root': {
      borderLeft: `1px solid ${alpha(hex, 0.15)}`,
      pl: 3,
    },
    // Shared accent overrides for inputs, checkboxes, switches, alerts
    ...createAccentOverrides(hex) as Record<string, unknown>,
    // Accordion-specific label override (neutral at rest, not tinted)
    '& .MuiInputLabel-root': {
      color: 'rgba(255, 255, 255, 0.4) !important',
    },
    // Accordion-specific alert overrides (icon and message coloring)
    '& .MuiAlert-icon': {
      color: `${hex} !important`,
    },
    '& .MuiAlert-message': {
      color: `${alpha(hex, 0.85)} !important`,
    },
  };
}

/** Accordion summary with transparent hover. */
export const accordionSummarySx: SxProps<Theme> = {
  '&:hover': { bgcolor: 'transparent' },
  minHeight: 56,
};

/** Small action button for accordion headers (right-aligned). */
export const accordionBtnSx: SxProps<Theme> = {
  textTransform: 'none',
  fontSize: '0.75rem',
  py: 0.5,
  ml: 'auto',
  minHeight: 32,
};
