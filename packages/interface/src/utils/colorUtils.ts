// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Color Utilities: Generate consistent hex colors for templates.
 * Sigma's WebGL renderer requires hex color strings.
 *
 * Uses the curated Chaos Cypher neon swatch palette so graph nodes
 * match the cyberpunk theme instead of random HSL colors.
 */

import { ChaosCypherSwatches , ChaosCypherNeutrals } from '../theme/palette';



/** Neutral slate used when no template id is available to hash. */
const DEFAULT_TEMPLATE_COLOR = ChaosCypherNeutrals.textSecondary;

/**
 * Generate a consistent hex color from a template ID.
 * Maps the ID to one of the 20 curated cyberpunk neon swatches
 * so template clusters have distinct, theme-consistent colors.
 * Returns `DEFAULT_TEMPLATE_COLOR` when the id is missing.
 */
export function getColorForTemplate(templateId: string | undefined | null): string {
  if (!templateId) return DEFAULT_TEMPLATE_COLOR;

  let hash = 0;
  for (let i = 0; i < templateId.length; i++) {
    hash = templateId.charCodeAt(i) + ((hash << 5) - hash);
    hash = hash & hash;
  }

  return ChaosCypherSwatches[Math.abs(hash) % ChaosCypherSwatches.length];
}

