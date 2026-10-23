// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Entity type helpers (spec 2026-10-07 § 6): a node's `entity_type` is one of
 * the eight core types and its template the source's core-type template;
 * anything more specific is the pack subtype in `properties.entity_subtype`.
 */

import { cleanTypeName } from './formatters';
import type { ExtractionDomain } from '../services/api/sourceProcessing';

/** The property a node's pack subtype is stored under. */
const SUBTYPE_PROPERTY = 'entity_subtype';

/** A record's `entity_subtype` (`character`), or undefined. */
export function entitySubtype(properties: Record<string, unknown> | null | undefined): string | undefined {
  const value = properties?.[SUBTYPE_PROPERTY];
  return typeof value === 'string' && value ? value : undefined;
}

/** `Person · Character`; the core type alone when there is no subtype. */
export function typeLabel(coreType: string | null | undefined, subtype?: string): string {
  const core = cleanTypeName(coreType);
  return subtype ? `${core} · ${cleanTypeName(subtype)}` : core;
}

/** `{entity_subtype: icon}` of every pack subtype that has its own icon (the first pack wins). */
export function subtypeIcons(domains: ExtractionDomain[] | undefined): Map<string, string> {
  const out = new Map<string, string>();
  for (const domain of domains ?? []) {
    for (const sub of domain.subtypes ?? []) {
      if (sub.icon && !out.has(sub.key)) out.set(sub.key, sub.icon);
    }
  }
  return out;
}
