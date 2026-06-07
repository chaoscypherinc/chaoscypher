// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Qualitative-band configurations for the dashboard's metric rulers.
 *
 * Bands are derived from real knowledge-graph benchmarks (OpenCyc 0.14%,
 * DBpedia ~0%, FB15k 0.27%, Wikidata avg-degree 6–40, etc.). They are NOT
 * targets — they are descriptive ranges. The ruler shows your value
 * relative to these ranges so you understand "where you sit" without
 * implying a goal.
 *
 * Sources for the cutoffs:
 * - Färber et al., "Which KG Is Best for Me?" (arXiv 1809.11099)
 * - Paulheim, "Towards Profiling Knowledge Graphs" (CEUR Vol-1927)
 * - General KG literature on density and avg-degree distributions.
 */

/**
 * Qualitative state for a band. Maps to the project's semantic palette in
 * the renderer (poor → error, ok → warning, good → success).
 */
export type BandState = 'poor' | 'ok' | 'good';

/** A single qualitative band. */
export interface BulletBand {
  /** Display label shown in tooltips ("Sparse", "Typical", etc.). */
  label: string;
  /**
   * Exclusive upper bound of this band. The last band's upperBound is the
   * scaleMax — values >= it land in that final band.
   */
  upperBound: number;
  /** Qualitative state — drives the ruler colour for this band's range. */
  state: BandState;
}

/** Configuration for a single metric ruler's band layout. */
export interface BulletConfig {
  /** Bands in ascending order of upperBound. */
  bands: BulletBand[];
  /** Maximum value rendered on the ruler. Values above clamp to 100% fill. */
  scaleMax: number;
}

/** Quality is a real 0–100 extraction-grade score. Bands are common UX practice. */
export const QUALITY_BANDS: BulletConfig = {
  bands: [
    { label: 'Poor', upperBound: 50, state: 'poor' },
    { label: 'OK', upperBound: 75, state: 'ok' },
    { label: 'Good', upperBound: 100, state: 'good' },
  ],
  scaleMax: 100,
};

/**
 * Density bands chosen to cover the realistic range of small-to-medium KGs.
 * Wikidata sits at ~10⁻⁹, OpenCyc at 0.14%, FB15k at 0.27%. Anything past 1%
 * is unusually dense; past 5% is "off the charts" for any non-trivial graph.
 */
export const DENSITY_BANDS: BulletConfig = {
  bands: [
    { label: 'Sparse', upperBound: 0.1, state: 'poor' },
    { label: 'Typical', upperBound: 1, state: 'ok' },
    { label: 'Dense', upperBound: 5, state: 'good' },
  ],
  scaleMax: 5,
};

/**
 * Avg-relationships-per-entity bands. Wikidata's linking degree is ~6.4,
 * DBpedia's ~21, OpenCyc's ~59. Below 3 reads as sparse; 3–10 is moderate;
 * above 10 is heavily linked (edge of the realistic range).
 */
export const AVG_REL_BANDS: BulletConfig = {
  bands: [
    { label: 'Sparse', upperBound: 3, state: 'poor' },
    { label: 'Moderate', upperBound: 10, state: 'ok' },
    { label: 'Heavy', upperBound: 30, state: 'good' },
  ],
  scaleMax: 30,
};

/**
 * Classify a numeric value into one of the configured bands.
 *
 * Returns the first band whose `upperBound` is greater than `value`. If
 * `value` exceeds every band's upperBound, returns the last band (so values
 * "off the top of the scale" still report as the highest qualitative range).
 */
export function classify(value: number, config: BulletConfig): BulletBand {
  for (const band of config.bands) {
    if (value < band.upperBound) {
      return band;
    }
  }
  return config.bands[config.bands.length - 1];
}

/**
 * Fill percentage (0–100) for the trail portion of the ruler, clamped at
 * the scale boundaries. Returns 0 when scaleMax is 0 (defensive — protects
 * against NaN/Infinity in the rendered style attribute).
 */
export function fillPct(value: number, config: BulletConfig): number {
  if (config.scaleMax <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(100, (value / config.scaleMax) * 100));
}

/**
 * Tick positions (0–100%) at every internal band boundary on a ruler.
 * Skips the final upperBound (which sits at the scale's right edge and
 * doesn't need a tick).
 */
export function tickPositions(config: BulletConfig): number[] {
  if (config.scaleMax <= 0) {
    return [];
  }
  return config.bands
    .slice(0, -1)
    .map((b) => (b.upperBound / config.scaleMax) * 100);
}
