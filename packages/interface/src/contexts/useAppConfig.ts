// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useAppConfig — read the operator-tunable backend settings exposed via
 * GET /api/v1/settings/public. Lives in its own file so PublicSettingsContext.tsx
 * (provider component) stays component-only for react-refresh.
 */
import { useContext } from 'react';
import type { PublicSettings } from '../services/api/publicSettings';
import { PublicSettingsContext } from './publicSettingsContextValue';

/** Returns the operator-tunable config. While loading or on error, returns sensible defaults. */
export function useAppConfig(): PublicSettings {
  return useContext(PublicSettingsContext);
}
