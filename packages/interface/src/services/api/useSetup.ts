// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for the first-run setup wizard's own data fetches.
 *
 * Introduced with the AccountStep migration off raw fetch+useEffect. Only the
 * access-hint read is migrated here — it tells the account step whether the
 * request arrived over loopback so the network-access switch can default
 * sensibly. The credential-creation submit (authApi.setup → completeSetup →
 * settingsApi.update) stays an imperative handler in the step because it's a
 * wizard-coupled side-effect chain, not a cacheable read.
 */

import { useQuery } from '@tanstack/react-query';

import { settingsApi } from './settings';

const ACCESS_HINT_QUERY_KEY = ['settings', 'access-hint'] as const;

export interface AccessHint {
  request_host: string;
  is_loopback: boolean;
}

/**
 * Where did this request come from? Drives the account step's "allow access
 * from other devices" default. `retry: false` keeps the single-attempt
 * behaviour of the old fetch so a failure falls through to the safe default
 * (external access off) without retry churn during setup.
 */
export function useAccessHint() {
  return useQuery<AccessHint>({
    queryKey: ACCESS_HINT_QUERY_KEY,
    queryFn: () => settingsApi.getAccessHint(),
    retry: false,
  });
}
