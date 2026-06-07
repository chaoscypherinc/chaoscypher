// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo, type ReactNode } from 'react';
import type { Settings } from '../types';
import { SettingsContext } from './settingsContextValue';

/**
 * Provides the loaded application settings to all child components.
 * This avoids duplicate settings fetches (e.g., App + Layout both calling settingsApi.get()).
 *
 * The refreshSettings callback allows consumers (e.g., SettingsPage) to trigger
 * a re-fetch so every component sees updated values without a full page reload.
 */
export function SettingsProvider({
  settings,
  refreshSettings,
  children,
}: {
  settings: Settings | null;
  refreshSettings: () => Promise<void>;
  children: ReactNode;
}) {
  const value = useMemo(
    () => ({ settings, refreshSettings }),
    [settings, refreshSettings],
  );

  return (
    <SettingsContext.Provider value={value}>
      {children}
    </SettingsContext.Provider>
  );
}
