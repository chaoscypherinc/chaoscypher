// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Themed accent styles — pure helpers for the visual treatment used by
 * <AccentSection /> and friends.
 *
 * Lives in its own file so the .tsx component file can be Fast-Refresh-clean
 * (only-export-components rule).
 */
import { alpha, type SxProps, type Theme } from '@mui/material';
import { ChaosCypherPalette } from './palette';

/** Named accent color presets — all derived from ChaosCypherPalette. */
export const ACCENT_COLORS: Record<string, string> = {
  file: ChaosCypherPalette.primary,
  domain: ChaosCypherPalette.warning,
  filtering: ChaosCypherPalette.success,
  settings: ChaosCypherPalette.purple,
  info: ChaosCypherPalette.accent,
  warning: ChaosCypherPalette.warning,
  error: ChaosCypherPalette.error,
};

/**
 * Resolve a named color preset to a hex value.
 * Passes through hex values unchanged. Returns undefined for no color.
 */
function resolveColor(color?: string): string | undefined {
  return (color && ACCENT_COLORS[color]) || color || undefined;
}

/**
 * Generate sx props for the border+tint accent treatment on a Box/section.
 *
 * @param color - Named preset or hex color. Omit for neutral treatment.
 */
export function accentSx(color?: string): SxProps<Theme> {
  const hex = resolveColor(color);
  if (!hex) {
    return {
      borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
      borderRadius: 0,
      p: 1.5,
    };
  }
  return {
    borderLeft: `2px solid ${alpha(hex, 0.4)}`,
    borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
    borderRadius: 0,
    pl: 2,
    py: 1.5,
    pr: 1.5,
  };
}

/**
 * Generate sx props for a MUI FormControl wrapping a Select with accent colors.
 *
 * Applies colored borders, bottom accent, and label coloring to the
 * MUI outlined input. Use on FormControl: `<FormControl sx={accentSelectSx('domain')}>`.
 *
 * @param color - Named preset or hex color.
 */
export function accentSelectSx(color: string): SxProps<Theme> {
  const hex = resolveColor(color);
  if (!hex) return {};
  return {
    '& .MuiOutlinedInput-root': {
      bgcolor: 'rgba(0, 0, 0, 0.2)',
      borderRadius: '4px',
    },
    '& .MuiOutlinedInput-notchedOutline': {
      borderColor: 'rgba(255, 255, 255, 0.08)',
    },
    '& .MuiInputLabel-root': {
      color: 'rgba(255, 255, 255, 0.4)',
    },
    '& .MuiInputLabel-root.Mui-focused': {
      color: alpha(hex, 0.8),
    },
    '&:hover .MuiOutlinedInput-notchedOutline': {
      borderColor: alpha(hex, 0.3),
    },
    '& .MuiOutlinedInput-root.Mui-focused .MuiOutlinedInput-notchedOutline': {
      borderColor: alpha(hex, 0.6),
      borderWidth: 1,
    },
  };
}

/**
 * Create shared accent color overrides for child MUI components.
 *
 * Generates CSS-in-JS rules for inputs, labels, checkboxes, switches,
 * and alerts that inherit an accent color. Used by both accentPaperSx
 * and accentAccordionSx to avoid duplication.
 *
 * @param hex - Resolved hex color value.
 */
export function createAccentOverrides(hex: string): SxProps<Theme> {
  return {
    // Child inputs: ghost style with accent focus
    '& .MuiOutlinedInput-root': {
      bgcolor: 'rgba(0, 0, 0, 0.2)',
      borderRadius: '4px',
    },
    '& .MuiOutlinedInput-root .MuiOutlinedInput-notchedOutline': {
      borderColor: 'rgba(255, 255, 255, 0.08) !important',
    },
    '& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline': {
      borderColor: `${alpha(hex, 0.3)} !important`,
    },
    '& .MuiOutlinedInput-root.Mui-focused .MuiOutlinedInput-notchedOutline': {
      borderColor: `${alpha(hex, 0.6)} !important`,
      borderWidth: '1px !important',
    },
    '& .MuiInputLabel-root': {
      color: `${alpha(hex, 0.5)} !important`,
    },
    '& .MuiInputLabel-root.Mui-focused': {
      color: `${alpha(hex, 0.8)} !important`,
    },
    // Checkboxes and switches inherit section color
    '& .MuiCheckbox-root.Mui-checked': {
      color: `${alpha(hex, 0.7)} !important`,
    },
    '& .MuiSwitch-switchBase.Mui-checked': {
      color: `${alpha(hex, 0.8)} !important`,
    },
    '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': {
      backgroundColor: `${alpha(hex, 0.4)} !important`,
    },
    // Alerts: ghost with accent tint
    '& .MuiAlert-root': {
      bgcolor: `${alpha(hex, 0.08)} !important`,
      border: `1px solid ${alpha(hex, 0.2)} !important`,
    },
  };
}

/**
 * Generate sx props for a MUI Paper section with accent colors.
 *
 * Applies colored border, bottom accent, and tinted background to a
 * Paper component. Use on Paper: `<Paper sx={accentPaperSx('settings')}>`.
 *
 * @param color - Named preset or hex color.
 */
export function accentPaperSx(color: string): SxProps<Theme> {
  const hex = resolveColor(color);
  if (!hex) return {};
  return {
    bgcolor: 'transparent',
    border: 'none',
    borderLeft: `2px solid ${alpha(hex, 0.4)}`,
    borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
    borderRadius: 0,
    boxShadow: 'none',
    ...createAccentOverrides(hex) as Record<string, unknown>,
  };
}
