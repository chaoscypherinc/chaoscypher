// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';

import { fetchPublicSettings } from '../services/api/publicSettings';
import { DEFAULT_PUBLIC_SETTINGS, PublicSettingsContext } from './publicSettingsContextValue';

const STALE_TIME_MS = 5 * 60 * 1000; // 5 min — config rarely changes; SSE channel can be added later.

/**
 * Bootstrap-fetches GET /api/v1/settings/public via React Query and exposes
 * the result through PublicSettingsContext. Children read it via useAppConfig().
 *
 * The provider value falls back to DEFAULT_PUBLIC_SETTINGS while the fetch is
 * in flight or if it fails — components never see `null`/`undefined` config.
 */
export function PublicSettingsProvider({ children }: { children: ReactNode }) {
  const { data } = useQuery({
    queryKey: ['public-settings'],
    queryFn: fetchPublicSettings,
    staleTime: STALE_TIME_MS,
    gcTime: STALE_TIME_MS * 2,
    retry: 1,
  });

  return (
    <PublicSettingsContext.Provider value={data ?? DEFAULT_PUBLIC_SETTINGS}>
      {children}
    </PublicSettingsContext.Provider>
  );
}
