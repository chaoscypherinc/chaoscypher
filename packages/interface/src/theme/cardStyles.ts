// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Reusable card style theme for consistent, elegant UI cards
 *
 * This utility creates subtle, elegant card styles with:
 * - 10% opacity background color
 * - 30% opacity border (1px)
 * - 20% opacity hover state
 * - Smooth lift animation on hover
 * - Optional clickable/non-clickable variants
 *
 * Usage:
 * ```tsx
 * import { getCardStyle } from '../theme/cardStyles';
 *
 * // Clickable card
 * <Box sx={getCardStyle('#2196f3', true)}>...</Box>
 *
 * // Non-clickable info card
 * <Box sx={getCardStyle('#4caf50', false)}>...</Box>
 *
 * // With custom overrides
 * <Box sx={{ ...getCardStyle('#ff9800'), p: 3 }}>...</Box>
 * ```
 */

/**
 * Convert hex color to rgba with specified alpha
 */
export const hexToRgba = (hex: string, alpha: number): string => {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
};

/**
 * Create elegant card styles with subtle borders
 *
 * @param color - Hex color code (e.g., '#2196f3')
 * @param clickable - Whether the card should have hover effects and cursor pointer
 * @returns MUI sx prop object with card styles
 */
export const getCardStyle = (color: string, clickable: boolean = true) => ({
  p: 1.5,
  borderRadius: 1.5,
  bgcolor: hexToRgba(color, 0.1), // 10% opacity for subtle background
  border: `1px solid ${hexToRgba(color, 0.3)}`, // 30% opacity for visible but subtle border
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: 0.5,
  cursor: clickable ? 'pointer' : 'default',
  transition: 'all 0.2s ease-in-out',
  position: 'relative',
  ...(clickable && {
    '&:hover': {
      bgcolor: hexToRgba(color, 0.2), // 20% opacity on hover for feedback
      transform: 'translateY(-2px)', // Subtle lift effect
      boxShadow: 2, // Add shadow on hover
    }
  })
});

/**
 * Canonical card/panel surface for the Source-detail tabs.
 *
 * A dark, translucent obsidian fill that sinks into the `#0A0E17` app
 * background instead of floating above it like a white overlay. This is the
 * single surface treatment shared across the Overview / Chunks / Extraction
 * tabs (knowledge map, distribution charts, pipeline stat cards, chunk tiles,
 * extraction cards…) so they all read uniformly. The heavier `glassPanelSx`
 * (with blur) stays reserved for large grouping panels; the `StatTile` glass
 * tiles are the same family at the same depth.
 *
 * The raw `SURFACE_BG` / `SURFACE_BORDER` values are exported for the few
 * call sites that compose them into a gradient or a partial override.
 */
export const SURFACE_BG = 'rgba(5, 5, 10, 0.25)';
export const SURFACE_BORDER = 'rgba(255, 255, 255, 0.08)';

export const surfaceSx = {
  background: SURFACE_BG,
  border: `1px solid ${SURFACE_BORDER}`,
  borderRadius: 1.5,
} as const;

/** Hover state for interactive (clickable) surfaces. */
export const surfaceHoverSx = {
  borderColor: 'rgba(255, 255, 255, 0.18)',
  background: 'rgba(5, 5, 10, 0.4)',
} as const;

/**
 * Frosted glass panel style for grouping related content.
 *
 * Creates a deeply dark, translucent obsidian panel with a blur effect
 * that lets background content bleed through while keeping text readable.
 *
 * Usage:
 * ```tsx
 * <Box sx={glassPanelSx}>...</Box>
 * <Box sx={{ ...glassPanelSx, p: 3 }}>...</Box>  // with overrides
 * ```
 */
export const glassPanelSx = {
  background: 'rgba(5, 5, 10, 0.4)',
  backdropFilter: 'blur(16px)',
  WebkitBackdropFilter: 'blur(16px)',
  border: '1px solid rgba(255, 255, 255, 0.06)',
  borderRadius: 1.5,
  p: 2,
} as const;

/**
 * Predefined color palette for common card types.
 *
 * All values derive from ChaosCypherPalette — the single source of truth.
 */
import { ChaosCypherPalette } from './palette';

export const CardColors = {
  primary:   ChaosCypherPalette.primary,
  secondary: ChaosCypherPalette.secondary,
  success:   ChaosCypherPalette.success,
  warning:   ChaosCypherPalette.warning,
  error:     ChaosCypherPalette.error,
  info:      ChaosCypherPalette.info,
  purple:    ChaosCypherPalette.purple,
  orange:    ChaosCypherPalette.orange,
  accent:    ChaosCypherPalette.accent,
} as const;

