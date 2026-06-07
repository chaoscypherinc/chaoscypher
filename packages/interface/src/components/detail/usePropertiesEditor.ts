// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useState } from 'react';

interface UsePropertiesEditorReturn {
  /** Current value of the "new property name" input. */
  newPropertyKey: string;
  /** Setter for the new-property-name input. */
  setNewPropertyKey: (key: string) => void;
  /** Immutably set `properties[key] = value`. */
  handleChange: (key: string, value: unknown) => void;
  /** Add a new property with the current `newPropertyKey` (empty string value). */
  handleAdd: () => void;
  /** Immutably remove `properties[key]`. */
  handleRemove: (key: string) => void;
}

/**
 * State and handlers for editing a `Record<string, unknown>` properties bag.
 * Used by the shared `PropertiesEditor` component and by pages that render
 * properties editing inline.
 *
 * @param properties Current properties object (may be undefined).
 * @param onChange Callback invoked with the next properties object.
 */
export function usePropertiesEditor(
  properties: Record<string, unknown> | undefined,
  onChange: (next: Record<string, unknown>) => void,
): UsePropertiesEditorReturn {
  const [newPropertyKey, setNewPropertyKey] = useState('');

  const handleChange = useCallback(
    (key: string, value: unknown) => {
      onChange({ ...(properties ?? {}), [key]: value });
    },
    [properties, onChange],
  );

  const handleAdd = useCallback(() => {
    if (!newPropertyKey.trim()) return;
    onChange({ ...(properties ?? {}), [newPropertyKey]: '' });
    setNewPropertyKey('');
  }, [newPropertyKey, properties, onChange]);

  const handleRemove = useCallback(
    (key: string) => {
      const next = { ...(properties ?? {}) };
      delete next[key];
      onChange(next);
    },
    [properties, onChange],
  );

  return {
    newPropertyKey,
    setNewPropertyKey,
    handleChange,
    handleAdd,
    handleRemove,
  };
}
