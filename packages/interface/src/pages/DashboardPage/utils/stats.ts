// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Pure stat-derivation helpers for the dashboard HUD.
 *
 * All functions return 0 for empty / undefined inputs so the UI renders
 * sensible zeros instead of NaN / Infinity for fresh databases.
 */

/** Average number of relationships per entity (relations / entities), rounded to 1 decimal. */
export function computeAvgRelations(entities: number, relations: number): number {
  if (entities <= 0 || relations <= 0) {
    return 0;
  }
  return parseFloat((relations / entities).toFixed(1));
}

/**
 * Directed-graph density as a percentage: m / (n × (n − 1)) × 100.
 *
 * Returns 0 when the graph is too small to meaningfully measure (n < 2 or m = 0).
 * Clamped at [0, 100] for safety; in practice real KGs sit far below 5%.
 */
export function computeDensity(entities: number, relations: number): number {
  if (entities < 2 || relations <= 0) {
    return 0;
  }
  const maxEdges = entities * (entities - 1);
  const pct = (relations / maxEdges) * 100;
  const clamped = Math.max(0, Math.min(100, pct));
  return parseFloat(clamped.toFixed(1));
}

/** Average number of relationships extracted per source document, rounded to 1 decimal. */
export function computeEdgesPerSource(sources: number, relations: number): number {
  if (sources <= 0 || relations <= 0) {
    return 0;
  }
  return parseFloat((relations / sources).toFixed(1));
}
