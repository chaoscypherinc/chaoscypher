// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for the TLS / HTTPS accordion in the General Settings
 * tab.
 *
 * `useTlsStatus` reads whether TLS is currently enabled; `useToggleTls`
 * enables self-signed TLS (with an optional hostname) or disables it, then
 * invalidates the status so the chip updates. TLS config is optional — the
 * status query is allowed to error silently and the accordion renders an
 * "Unknown" state (no chip) until it resolves.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { tlsApi } from '../../../services/api/tls';

const TLS_STATUS_QUERY_KEY = ['settings', 'tls', 'status'] as const;

export function useTlsStatus() {
  return useQuery<boolean>({
    queryKey: TLS_STATUS_QUERY_KEY,
    queryFn: async () => {
      const status = await tlsApi.getStatus();
      return status.enabled;
    },
  });
}

/**
 * Toggle TLS. When `enable` is true a self-signed cert is provisioned with the
 * optional `hostname`; otherwise TLS is disabled. Returns the new enabled flag.
 */
export function useToggleTls() {
  const qc = useQueryClient();
  return useMutation<boolean, Error, { enable: boolean; hostname?: string }>({
    mutationFn: async ({ enable, hostname }) => {
      if (enable) {
        await tlsApi.enableSelfSigned(hostname);
        return true;
      }
      await tlsApi.disable();
      return false;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TLS_STATUS_QUERY_KEY });
    },
  });
}
