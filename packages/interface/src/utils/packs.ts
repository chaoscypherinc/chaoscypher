// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Pack helpers shared by the pack chips (PackSelect), the source's packs chip
 * (PacksChip), the confirm dialogs and the source settings (spec 2026-10-07
 * § 5): a source extracts with the floor every source has plus zero or more
 * packs; `[]` is the floor alone.
 */

import type { ExtractionDomain } from '../services/api/sourceProcessing';
import type { UnifiedSource } from '../types';

/** The floor plugin's name: stored rows and the API read it as "no pack". */
export const FLOOR_DOMAIN = 'generic';

const SEPARATOR = '+';

export function capitalizePack(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** `Historical + Literary`; `Floor only` for no pack. */
export function packsLabel(packs: string[]): string {
  return packs.length === 0 ? 'Floor only' : packs.map(capitalizePack).join(' + ');
}

/** The domains a source may add: every registered pack (never the floor). */
export function packsOf(domains: ExtractionDomain[]): ExtractionDomain[] {
  return domains.filter((d) => (d.role ?? 'pack') === 'pack' && d.name !== FLOOR_DOMAIN);
}

/** What a source row says about its packs (list first, the old `+` label after). */
export interface SourcePacksFields {
  extraction_packs?: string[] | null;
  extraction_domain?: string | null;
  extraction_domain_icon?: string | null;
  extraction_pack_icons?: Record<string, string | null> | null;
}

/**
 * The packs a source was extracted with, in its stored order:
 * `extraction_packs` when present, else the `extraction_domain` label split on
 * `+`; the floor's name is no pack. `null` when the source names nothing yet.
 */
export function sourcePacks(source: SourcePacksFields): string[] | null {
  if (Array.isArray(source.extraction_packs)) {
    return source.extraction_packs.filter((p) => p && p !== FLOOR_DOMAIN);
  }
  if (!source.extraction_domain) return null;
  return source.extraction_domain
    .split(SEPARATOR)
    .map((p) => p.trim())
    .filter((p) => p && p !== FLOOR_DOMAIN);
}

/** The icon a pack shows for a source (its `extraction_pack_icons`, else the one-domain icon). */
export function packIconOf(source: SourcePacksFields, pack: string): string | null | undefined {
  return source.extraction_pack_icons?.[pack] ?? (
    source.extraction_domain === pack ? source.extraction_domain_icon : undefined
  );
}

/**
 * The packs a detection proposal names, in its (score) order: `packs` when the
 * proposal carries the list; else, for a proposal written before packs, its one
 * `detected_domain` (or `domain`), else the ranking's first when detection was
 * confident. The floor's name is no pack, and only registered packs count.
 */
export function proposedPacks(
  source: Pick<
    UnifiedSource,
    'proposed_extraction_options' | 'detection_ranking' | 'detection_low_confidence'
  > | null,
  availableDomains: ExtractionDomain[],
): string[] {
  const proposal = source?.proposed_extraction_options;
  const known = new Set(packsOf(availableDomains).map((d) => d.name));
  const one = proposal?.detected_domain ?? proposal?.domain;
  const top = source?.detection_low_confidence ? undefined : source?.detection_ranking?.[0]?.domain;
  const listed = proposal?.packs ?? (one ? [one] : top ? [top] : []);
  return listed.filter((name) => known.has(name));
}
