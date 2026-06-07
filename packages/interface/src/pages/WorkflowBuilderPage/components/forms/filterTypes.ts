// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Filter types and JSON converters for the workflow FilterBuilder.
 *
 * Lives in its own file so FilterBuilder.tsx is Fast-Refresh-clean.
 */

/**
 * Single filter rule
 */
export interface FilterRule {
  id: string;
  field: string;
  operator: FilterOperator;
  value: string;
}

/**
 * Filter operators
 */
export type FilterOperator =
  | 'equals'
  | 'not_equals'
  | 'contains'
  | 'starts_with'
  | 'ends_with'
  | 'regex';

function generateId(): string {
  return `filter-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Convert FilterRules to backend JSON format
 */
export function filtersToJson(filters: FilterRule[]): Record<string, unknown> {
  if (filters.length === 0) return {};

  const result: Record<string, unknown> = {};

  for (const filter of filters) {
    if (!filter.field) continue;

    // Simple equality - just use the value directly
    if (filter.operator === 'equals') {
      result[filter.field] = filter.value;
    } else {
      // Complex operator - use object format
      result[filter.field] = {
        operator: filter.operator,
        value: filter.value,
      };
    }
  }

  return result;
}

/**
 * Convert backend JSON to FilterRules
 */
export function jsonToFilters(json: Record<string, unknown> | null): FilterRule[] {
  if (!json || typeof json !== 'object') return [];

  const filters: FilterRule[] = [];

  for (const [field, value] of Object.entries(json)) {
    if (typeof value === 'object' && value !== null && 'operator' in value) {
      // Complex format with operator
      filters.push({
        id: generateId(),
        field,
        operator:
          ((value as Record<string, string>).operator as FilterOperator) || 'equals',
        value: (value as Record<string, string>).value || '',
      });
    } else {
      // Simple equality
      filters.push({
        id: generateId(),
        field,
        operator: 'equals',
        value: String(value),
      });
    }
  }

  return filters;
}
