// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useSettings — hook to access the shared application settings.
 *
 * Must be used within a <SettingsProvider />.
 */
import { useContext } from 'react';
import type { Settings } from '../types';
import { SettingsContext } from './settingsContextValue';

/**
 * Narrows {@link SettingsContextValue.settings} from `Settings | null` to
 * `Settings`. SettingsProvider holds `null` during the unauthenticated
 * bootstrap window — only public auth pages mount in that window, and
 * none of them call this hook.
 */
export function useSettings(): { settings: Settings; refreshSettings: () => Promise<void> } {
  const context = useContext(SettingsContext);
  if (!context) {
    throw new Error('useSettings must be used within a SettingsProvider');
  }
  if (!context.settings) {
    throw new Error('useSettings called before settings were loaded');
  }
  return { settings: context.settings, refreshSettings: context.refreshSettings };
}
