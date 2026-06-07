// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Master color registry for the application.
 *
 * All domain-specific color mappings live here. Components import
 * named constants instead of hardcoding hex/rgba values.
 *
 * All colors derive from ChaosCypherPalette (via CardColors or directly).
 * See palette.ts for the single source of truth.
 *
 * For card styling utilities see cardStyles.ts.
 * For chat-specific styling see chatTheme.ts.
 */

import { CardColors } from './cardStyles';
import { ChaosCypherPalette } from './palette';

/** Workflow tool category colors. */
export const CategoryColors: Record<string, string> = {
  ai: CardColors.secondary,
  graph: CardColors.primary,
  logic: CardColors.warning,
  data: CardColors.success,
  http: CardColors.accent,
  external: CardColors.accent,
  templates: CardColors.info,
  template: CardColors.info,
};

/** Schema / form field data type colors. */
export const DataTypeColors: Record<string, string> = {
  string: CardColors.primary,
  number: CardColors.success,
  integer: CardColors.success,
  boolean: CardColors.warning,
  object: CardColors.secondary,
  array: CardColors.accent,
  any: CardColors.info,
};

/** Source processing pipeline stage colors. */
export const StageColors = {
  indexing: CardColors.success,
  extraction: CardColors.primary,
  commit: CardColors.secondary,
  empty: '#3a3a3a',
} as const;

/**
 * Pipeline funnel stage identity colors (Processing tab).
 * Calm, per-stage hues used for pill rings, icons, and the stage stats board.
 * Severity (below) only overrides these when something actually went wrong.
 */
export const PipelineStageColors = {
  load: '#7eb3d4',
  clean: '#7eb3d4',
  chunk: '#7eb3d4',
  extract: '#d99cd3',
  filter: '#d99563',
  commit: '#7fcc84',
} as const;

/** Pipeline severity overrides — reserved for genuine problems, not normal removals. */
export const PipelineSeverityColors = {
  ok: '#5b9a5f',
  warn: '#ffa726',
  err: '#ef5350',
} as const;

/** Knowledge graph content type colors. */
export const ContentTypeColors = {
  entities: CardColors.primary,
  relationships: CardColors.error,
  templates: CardColors.orange,
  automations: CardColors.info,
  chunks: CardColors.accent,
  time: '#9e9e9e',
} as const;

/** System health / queue status indicator colors. */
export const StatusColors = {
  healthy: CardColors.success,
  active: CardColors.warning,
  warning: CardColors.warning,
  failed: CardColors.error,
  neutral: '#757575',
} as const;

/**
 * Chunks-tab section accent — a softened cyan (`#5fd0ff`, gentler than the
 * neon `primary`). Shared by the per-chunk Overview band and the Document
 * Chunks list below it so the two areas read as one continuous zone instead
 * of a hard cyan-to-grey seam.
 */
export const ChunkAccent = {
  /** Hairline border around the band and the chunk papers. */
  border: 'rgba(95, 208, 255, 0.2)',
  /** Faint wash behind the band and the chunk papers. */
  bg: 'rgba(95, 208, 255, 0.04)',
  /** Crisp left-edge accent on the chunk cards — connects them to the band
      without washing the card surface. */
  edge: 'rgba(95, 208, 255, 0.6)',
  /** Solid accent for selection rings / markers (tiles, status rail). */
  ring: '#5fd0ff',
  /** Heading text inside the band. */
  heading: '#cdeeff',
  /** Keyboard-focus ring for the band's disclosures. */
  focus: 'rgba(95, 208, 255, 0.6)',
} as const;

/** Context window segment colors (shared by all context breakdown/utilization charts). */
export const ContextColors = {
  system: ChaosCypherPalette.secondary,
  input: ChaosCypherPalette.primary,
  output: ChaosCypherPalette.accent,
  outputCap: ChaosCypherPalette.error,
} as const;

/** Quality score grade colors. */
export const QualityColors = {
  grades: {
    outstanding: '#00FFB2',
    excellent: '#00B8FF',
    good: ChaosCypherPalette.accent,
    fair: ChaosCypherPalette.warning,
    low: ChaosCypherPalette.error,
  },
  // Muted, desaturated section accents — kept softer than the vivid grade palette so the breakdown matches the rest of the Source-detail area.
  sections: {
    entity: '#7fcc84',
    relationship: '#7eb3d4',
    connectivity: '#8c9fd4',
    finalGrade: '#c9a86a',
    richness: '#bfa3d6',
    penalty: '#d98a8a',
  },
  defaultGray: '#94a3b8',
  outstandingGradient: `linear-gradient(135deg, ${ChaosCypherPalette.primary}, ${ChaosCypherPalette.secondary})`,
  outstandingBorder: ChaosCypherPalette.primary,
} as const;

/** Graph canvas rendering colors. */
export const GraphColors = {
  light: {
    label: '#333333',
    edgeLabel: '#666666',
    edge: '#cccccc',
    background: '#f5f5f5',
  },
  dark: {
    label: '#e0e0e0',
    edgeLabel: '#b0b0b0',
    edge: '#555555',
    background: '#0A0E17',
  },
  fadedNode: '#888888',
  fadedFallback: '#999',
  popupBase: '#616161',
} as const;

/** Tag color picker palette (neon-flavored). */
export const TagPalette = [
  '#FF003C', '#FF0080', '#BF00FF', '#7C4DFF',
  '#448AFF', '#00E5FF', '#00BFA5', '#00E676',
  '#76FF03', '#FFD600', '#FFB300', '#FF6D00',
  '#FF3D00', '#E040FB', '#18FFFF', '#69F0AE',
] as const;

/** Text highlight background (search results, inline matches). */
export const HighlightColor = '#1A3A4A';

/** Source domain badge colors. */
export const DomainColors = {
  auto: CardColors.purple,
  manual: CardColors.primary,
} as const;

/** Returns the theme color for a given queue name. */
export function getQueueColor(queue: string): string {
  if (queue === 'llm') return CardColors.primary;
  if (queue === 'operations') return CardColors.orange;
  return StatusColors.neutral;
}

/** Returns a human-readable display name for a queue. */
export function getQueueDisplayName(queue: string): string {
  if (queue === 'llm') return 'LLM Queue';
  if (queue === 'operations') return 'Operations Queue';
  return queue;
}
