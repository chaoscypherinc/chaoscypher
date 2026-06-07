// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Dark/light mode aware overlay constants.
 *
 * Many components use rgba(white) in dark mode and rgba(black) in light mode
 * for subtle backgrounds, borders, and hover states. This file centralizes
 * those paired values to eliminate scattered rgba strings.
 *
 * Usage with MUI sx:
 * ```tsx
 * backgroundColor: (theme) =>
 *   theme.palette.mode === 'dark' ? Overlays.subtle.dark : Overlays.subtle.light,
 * ```
 */

/** Paired dark/light overlay values at standard strengths. */
export const Overlays = {
  /** Barely visible tint — paper/card backgrounds. */
  subtle: { dark: 'rgba(255, 255, 255, 0.02)', light: 'rgba(0, 0, 0, 0.02)' },
  /** Light tint — input field backgrounds. */
  light: { dark: 'rgba(255, 255, 255, 0.05)', light: 'rgba(0, 0, 0, 0.03)' },
  /** Slightly stronger — secondary hover, focused input bg. */
  lightHover: { dark: 'rgba(255, 255, 255, 0.08)', light: 'rgba(0, 0, 0, 0.05)' },
  /** Border default strength. */
  border: { dark: 'rgba(255, 255, 255, 0.12)', light: 'rgba(0, 0, 0, 0.12)' },
  /** Border hover strength. */
  borderHover: { dark: 'rgba(255, 255, 255, 0.2)', light: 'rgba(0, 0, 0, 0.2)' },
  /** Prominent white on dark backgrounds. */
  prominent: { dark: 'rgba(255, 255, 255, 0.7)', light: 'rgba(0, 0, 0, 0.6)' },
  /** Near-opaque white on dark backgrounds. */
  strong: { dark: 'rgba(255, 255, 255, 0.9)', light: 'rgba(0, 0, 0, 0.87)' },
} as const;
