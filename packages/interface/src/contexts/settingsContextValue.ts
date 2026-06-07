// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Settings context value type and React context object.
 *
 * Lives in its own file so SettingsContext.tsx (component) and useSettings.ts
 * (hook) can both consume it without tripping the react-refresh
 * only-export-components rule.
 */
import { createContext } from 'react';
import type { Settings } from '../types';

/**
 * Internal value held by the SettingsContext.
 *
 * `settings` may be null during the unauthenticated bootstrap window
 * (between auth-state determined and login completing). Components reach
 * through `useSettings()`, which narrows the type and throws when settings
 * are accessed without being loaded — public auth pages (/setup, /login)
 * never call `useSettings`, so they render fine while settings is null.
 */
interface SettingsContextValue {
  settings: Settings | null;
  refreshSettings: () => Promise<void>;
}

export const SettingsContext = createContext<SettingsContextValue | null>(null);
