// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useState } from 'react';

export interface UsePropertyEditorReturn {
  /** Current properties bag. */
  properties: Record<string, unknown>;
  /** Current value of the "new property name" input. */
  newPropertyKey: string;
  /** Setter for the new-property-name input. */
  setNewPropertyKey: (key: string) => void;
  /** Active tab index (0 = Properties, 1 = Raw JSON). */
  activeTab: number;
  /** Switch between Properties and Raw JSON tabs. */
  setActiveTab: (tab: number) => void;
  /** Immutably set `properties[key] = value`. */
  handlePropertyChange: (key: string, value: unknown) => void;
  /** Add a new property using the current `newPropertyKey` (empty string value). */
  handleAddProperty: () => void;
  /** Immutably remove `properties[key]`. */
  handleRemoveProperty: (key: string) => void;
  /** Parse a JSON string and replace the entire properties bag. Invalid JSON is ignored. */
  handleJsonChange: (jsonString: string) => void;
  /** Replace the entire properties bag (e.g. when switching edit targets). */
  setProperties: (props: Record<string, unknown>) => void;
}

/**
 * State and handlers for editing a `Record<string, unknown>` properties bag
 * via a tabbed Properties / Raw JSON panel.
 *
 * Used by CRUD page dialogs (NodesPage, EdgesPage, etc.) where the properties
 * editor owns its own state rather than receiving it externally.
 */
export function usePropertyEditor(
  initialProperties: Record<string, unknown> = {},
): UsePropertyEditorReturn {
  const [properties, setProperties] = useState<Record<string, unknown>>(initialProperties);
  const [newPropertyKey, setNewPropertyKey] = useState('');
  const [activeTab, setActiveTab] = useState(0);

  const handlePropertyChange = useCallback((key: string, value: unknown) => {
    setProperties((prev) => ({ ...prev, [key]: value }));
  }, []);

  const handleAddProperty = useCallback(() => {
    if (!newPropertyKey.trim()) return;
    setProperties((prev) => ({ ...prev, [newPropertyKey]: '' }));
    setNewPropertyKey('');
  }, [newPropertyKey]);

  const handleRemoveProperty = useCallback((key: string) => {
    setProperties((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const handleJsonChange = useCallback((jsonString: string) => {
    try {
      const parsed = JSON.parse(jsonString);
      setProperties(parsed);
    } catch {
      // Invalid JSON — do not update
    }
  }, []);

  return {
    properties,
    newPropertyKey,
    setNewPropertyKey,
    activeTab,
    setActiveTab,
    handlePropertyChange,
    handleAddProperty,
    handleRemoveProperty,
    handleJsonChange,
    setProperties,
  };
}
