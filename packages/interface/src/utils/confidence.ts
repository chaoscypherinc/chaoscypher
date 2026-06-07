// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { ChaosCypherPalette } from '../theme/palette';

/**
 * Normalize a raw confidence value to a 0–1 fraction. Extraction confidence is
 * stored as a 0–1 float, but guard against values already expressed as a
 * 0–100 percentage by scaling anything above 1. Returns `null` for missing or
 * non-numeric input.
 */
export function normalizeConfidence(value: unknown): number | null {
  if (typeof value !== 'number' || Number.isNaN(value)) return null;
  const fraction = value > 1 ? value / 100 : value;
  // Clamp into [0, 1] so a stray out-of-range value can't produce e.g. "140%".
  return Math.min(1, Math.max(0, fraction));
}

/**
 * Format a raw confidence value as a whole-percent string (e.g. "87%"), or
 * `null` when there is no usable value.
 */
export function formatConfidencePct(value: unknown): string | null {
  const fraction = normalizeConfidence(value);
  if (fraction === null) return null;
  return `${Math.round(fraction * 100)}%`;
}

/**
 * Threshold-graded accent colour for a confidence chip: mint ≥ 80%, gold ≥
 * 50%, red below. Returns the neutral cyan when there is no value.
 */
export function confidenceChipColor(value: unknown): string {
  const fraction = normalizeConfidence(value);
  if (fraction === null) return ChaosCypherPalette.primary;
  if (fraction >= 0.8) return ChaosCypherPalette.success;
  if (fraction >= 0.5) return ChaosCypherPalette.warning;
  return ChaosCypherPalette.error;
}
