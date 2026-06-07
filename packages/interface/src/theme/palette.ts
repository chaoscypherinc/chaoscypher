// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Chaos Cypher master color palette.
 *
 * This is the SINGLE SOURCE OF TRUTH for all themed colors in the app.
 * Every color constant elsewhere (CardColors, ACCENT_COLORS, domain maps)
 * must reference this palette — never hardcode hex values.
 *
 * The first 6 entries map directly to MUI's built-in palette slots.
 * The last 3 are extras for domain needs MUI doesn't cover.
 */

/** Core application color palette — all themed colors derive from here. */
export const ChaosCypherPalette = {
  primary:   '#00E5FF',  // Neon Cyan — brand, links, focus, inputs
  secondary: '#FF0080',  // Neon Hot Pink — AI, chat, secondary actions
  error:     '#FF003C',  // Neon Red — errors, destructive, failed
  warning:   '#FFAB00',  // Neon Gold — caution, active, discovery
  info:      '#00BFA5',  // Neon Teal — workflows, informational
  success:   '#1DE9B6',  // Neon Mint — positive, healthy, import
  purple:    '#BF00FF',  // Neon Purple — lenses, settings, special
  orange:    '#FF6D00',  // Neon Orange — templates
  accent:    '#7C4DFF',  // Neon Violet — embeddings, external tools
} as const;

/** Custom background overrides for the Chaos Cypher theme. */
export const ChaosCypherBackground = {
  dark:  { default: '#0A0E17', paper: '#111827' },
  light: { default: '#F8FAFC', paper: '#FFFFFF' },
} as const;

/**
 * Neutral slate palette for text hierarchy, surfaces, and dividers.
 *
 * These are the de-facto Tailwind slate values that drifted across the
 * codebase before this palette existed. Now codified here and wired into
 * MUI's `palette.text.*` / `palette.divider` slots so components can use
 * `sx={{ color: 'text.primary' }}` instead of hardcoding hex.
 */
export const ChaosCypherNeutrals = {
  textPrimary:   '#e2e8f0', // slate-200 — primary text on dark backgrounds
  textSecondary: '#94a3b8', // slate-400 — secondary text / captions
  textTertiary:  '#64748b', // slate-500 — inactive tabs / placeholder
  textMuted:     '#475569', // slate-600 — muted labels / table headers
  borderDivider: '#334155', // slate-700 — heavy divider
  surfaceRaised: '#1e293b', // slate-800 — raised surface background
} as const;

/**
 * Master swatch list for color pickers (template color picker, future tag
 * picker alignment). Curated neon palette intentionally designed to have
 * distinguishable hues for rapid visual differentiation.
 *
 * Callers: `components/TemplateIconPicker.tsx`. Future candidate for tag
 * picker alignment.
 */
export const ChaosCypherSwatches = [
  '#00E5FF', '#FF0080', '#00E676', '#BF00FF', '#FFB300',
  '#7C4DFF', '#FF003C', '#00BFA5', '#FF6D00', '#E040FB',
  '#448AFF', '#76FF03', '#18FFFF', '#FF4081', '#B388FF',
  '#00B8D4', '#69F0AE', '#FFAB00', '#D500F9', '#FF6E40',
] as const;
