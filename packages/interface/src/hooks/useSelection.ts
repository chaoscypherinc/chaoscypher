// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Generic Selection State Hook
 *
 * Manages a set of selected item IDs with toggle, toggle-all,
 * clear, and membership-check operations. Uses Set internally
 * for O(1) lookups.
 */

import { useState, useCallback, useMemo } from 'react';

interface UseSelectionReturn<T extends string | number> {
  /** Set of currently selected IDs */
  selected: Set<T>;
  /** Toggle selection of a single item */
  toggle: (id: T) => void;
  /** Toggle all items: selects all if not all selected, clears if all selected */
  toggleAll: (ids: T[]) => void;
  /** Clear all selections */
  clear: () => void;
  /** Check if an item is selected */
  isSelected: (id: T) => boolean;
  /** Number of currently selected items */
  selectedCount: number;
}

/**
 * Hook for managing selection state in lists and tables
 *
 * Provides Set-based selection with efficient toggle operations.
 * The toggleAll function selects all provided IDs when not all
 * are currently selected, and clears selection when they all are.
 *
 * @typeParam T - The type of item IDs (string or number)
 * @returns Selection state and control functions
 *
 * @example
 * ```tsx
 * const selection = useSelection<string>();
 *
 * // Select-all checkbox
 * <Checkbox
 *   checked={selection.selectedCount === items.length && items.length > 0}
 *   indeterminate={selection.selectedCount > 0 && selection.selectedCount < items.length}
 *   onChange={() => selection.toggleAll(items.map(i => i.id))}
 * />
 *
 * // Per-row checkbox
 * {items.map(item => (
 *   <Checkbox
 *     key={item.id}
 *     checked={selection.isSelected(item.id)}
 *     onChange={() => selection.toggle(item.id)}
 *   />
 * ))}
 *
 * // Bulk action
 * <Button
 *   disabled={selection.selectedCount === 0}
 *   onClick={() => handleDelete([...selection.selected])}
 * >
 *   Delete ({selection.selectedCount})
 * </Button>
 * ```
 */
export function useSelection<T extends string | number = string>(): UseSelectionReturn<T> {
  const [selected, setSelected] = useState<Set<T>>(new Set());

  const toggle = useCallback((id: T) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const toggleAll = useCallback((ids: T[]) => {
    setSelected((prev) => {
      const allSelected = ids.length > 0 && ids.every((id) => prev.has(id));
      if (allSelected) {
        return new Set();
      }
      return new Set(ids);
    });
  }, []);

  const clear = useCallback(() => {
    setSelected(new Set());
  }, []);

  const isSelected = useCallback(
    (id: T) => selected.has(id),
    [selected]
  );

  const selectedCount = useMemo(() => selected.size, [selected]);

  return { selected, toggle, toggleAll, clear, isSelected, selectedCount };
}
