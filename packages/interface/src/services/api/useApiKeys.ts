// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for personal API keys.
 *
 * Introduced with the ApiKeysSettings migration off raw fetch+useState.
 * `useApiKeys` is the list query; `useCreateApiKey` / `useRevokeApiKey`
 * are the create/revoke mutations and invalidate the list on success so
 * the table re-syncs (the create response's one-time plaintext key is
 * returned to the caller for the reveal dialog, never cached).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { authApi, type ApiKeyInfo, type ApiKeyCreatedResponse } from './auth';

const API_KEYS_QUERY_KEY = ['auth', 'keys'] as const;

export function useApiKeys() {
  return useQuery<ApiKeyInfo[]>({
    queryKey: API_KEYS_QUERY_KEY,
    queryFn: () => authApi.listKeys(),
  });
}

export function useCreateApiKey() {
  const qc = useQueryClient();
  return useMutation<ApiKeyCreatedResponse, Error, string>({
    mutationFn: (name) => authApi.createKey(name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: API_KEYS_QUERY_KEY });
    },
  });
}

export function useRevokeApiKey() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (keyId) => authApi.revokeKey(keyId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: API_KEYS_QUERY_KEY });
    },
  });
}
