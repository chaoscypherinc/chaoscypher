// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared types and helpers for extraction sub-components.
 */

import { ChartPalette, ChartDefaultGrey } from '../../../../theme/charts';
import { cleanTypeName } from '../../../../utils/formatters';

/** Template data returned by the sources API for a single source. */
export interface SourceTemplate {
  id: string;
  name: string;
  description: string | null;
  template_type: string;
  properties: Array<{
    name: string;
    display_name?: string;
    property_type?: string;
    required?: boolean;
  }>;
  is_system: boolean;
  icon?: string | null;
  color?: string | null;
  source_id: string | null;
  node_count: number;
  edge_count: number;
  created_at: string;
  updated_at: string;
}

/** Generate a consistent color from a string via hashing into ChartPalette. */
export const stringToColor = (str: string): string => {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  return ChartPalette[Math.abs(hash) % ChartPalette.length];
};

/** Resolve a colour for a given entity type name. */
export const getTypeColor = (type: string | null): string => {
  if (!type) return ChartDefaultGrey;
  const clean = cleanTypeName(type).toLowerCase();
  return stringToColor(clean);
};

/** Default page size used across all extraction views. */
export const PAGE_SIZE = 50;
