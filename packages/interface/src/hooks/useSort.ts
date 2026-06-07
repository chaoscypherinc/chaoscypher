// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Generic Sort State Hook
 *
 * Manages sort field and direction state with toggle behavior.
 * When toggling the same field, the direction flips between asc and desc.
 * When selecting a new field, it resets to the default direction.
 */

import { useState, useCallback } from 'react';

type SortDirection = 'asc' | 'desc';

interface UseSortReturn<T extends string> {
  /** The currently active sort field */
  sortField: T;
  /** The current sort direction */
  sortDirection: SortDirection;
  /** Toggle sort on a field: flips direction if same field, sets default direction if new field */
  toggleSort: (field: T) => void;
  /** Directly set the sort field without toggling direction */
  setSortField: (field: T) => void;
  /** Directly set the sort direction */
  setSortDirection: (dir: SortDirection) => void;
}

/**
 * Hook for managing sort field and direction state
 *
 * Provides a toggleSort function that flips direction when clicking
 * the same column header, and resets to the default direction when
 * selecting a different column.
 *
 * @typeParam T - Union type of valid sort field names
 * @param defaultField - The initial sort field
 * @param defaultDirection - The initial sort direction (defaults to 'asc')
 * @returns Sort state and control functions
 *
 * @example
 * ```tsx
 * type SortField = 'name' | 'created_at' | 'status';
 *
 * const { sortField, sortDirection, toggleSort } = useSort<SortField>('name');
 *
 * // In table header
 * <TableSortLabel
 *   active={sortField === 'name'}
 *   direction={sortField === 'name' ? sortDirection : 'asc'}
 *   onClick={() => toggleSort('name')}
 * >
 *   Name
 * </TableSortLabel>
 *
 * // Sort data
 * const sorted = [...data].sort((a, b) => {
 *   const cmp = a[sortField].localeCompare(b[sortField]);
 *   return sortDirection === 'asc' ? cmp : -cmp;
 * });
 * ```
 */
export function useSort<T extends string>(
  defaultField: T,
  defaultDirection: SortDirection = 'asc'
): UseSortReturn<T> {
  const [sortField, setSortField] = useState<T>(defaultField);
  const [sortDirection, setSortDirection] = useState<SortDirection>(defaultDirection);

  const toggleSort = useCallback(
    (field: T) => {
      if (field === sortField) {
        setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortField(field);
        setSortDirection(defaultDirection);
      }
    },
    [sortField, defaultDirection]
  );

  return { sortField, sortDirection, toggleSort, setSortField, setSortDirection };
}
