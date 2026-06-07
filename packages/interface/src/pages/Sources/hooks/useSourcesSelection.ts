// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback } from 'react';
import { useSelection } from '../../../hooks/useSelection';
import type { UnifiedSource } from '../../../types';

interface UseSourcesSelectionReturn {
  selectedIds: Set<string>;
  isSelected: (id: string) => boolean;
  toggle: (id: string) => void;
  toggleAll: (sources: UnifiedSource[]) => void;
  deselectAll: () => void;
  selectedCount: number;
  getSelectedSources: (sources: UnifiedSource[]) => UnifiedSource[];
}

/**
 * Hook for managing source selection state.
 *
 * Thin wrapper around the generic {@link useSelection} hook,
 * adding Sources-specific convenience methods like filtering
 * selected sources from a list.
 */
export function useSourcesSelection(): UseSourcesSelectionReturn {
  const base = useSelection<string>();

  const toggleAll = useCallback(
    (sources: UnifiedSource[]) => {
      base.toggleAll(sources.map((s) => s.id));
    },
    [base],
  );

  const getSelectedSources = useCallback(
    (sources: UnifiedSource[]) => sources.filter((s) => base.selected.has(s.id)),
    [base.selected],
  );

  return {
    selectedIds: base.selected,
    isSelected: base.isSelected,
    toggle: base.toggle,
    toggleAll,
    deselectAll: base.clear,
    selectedCount: base.selectedCount,
    getSelectedSources,
  };
}
