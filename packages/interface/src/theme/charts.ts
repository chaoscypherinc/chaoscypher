// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Chart color palettes for Recharts and Chart.js visualizations.
 *
 * Centralizes the color arrays used across OverviewTab, ExtractionTab,
 * and other charting components. Neon-flavored for the Chaos Cypher theme.
 */

/** General-purpose chart palette (16 distinct neon colors). */
export const ChartPalette = [
  '#00E5FF', '#FF0080', '#00E676', '#BF00FF',
  '#FFB300', '#7C4DFF', '#FF003C', '#00BFA5',
  '#FF6D00', '#448AFF', '#E040FB', '#76FF03',
  '#FF4081', '#18FFFF', '#FFAB00', '#B388FF',
] as const;

/** Categorical palette for pie/donut/bar charts (8 colors). */
export const CategoricalPalette = [
  '#00E5FF', '#FF0080', '#00E676', '#BF00FF',
  '#FFB300', '#7C4DFF', '#FF003C', '#00BFA5',
] as const;

/** Default grey for unrecognized chart categories. */
export const ChartDefaultGrey = '#999';

/** Tooltip styling constants shared across chart components. */
export const ChartTooltip = {
  background: 'rgba(0, 0, 0, 0.9)',
  text: '#ffffff',
  border: 'rgba(255, 255, 255, 0.2)',
  tick: '#999',
  /** Hover cursor highlight behind the active bar/point — subtle on dark. */
  cursor: 'rgba(255, 255, 255, 0.06)',
} as const;
