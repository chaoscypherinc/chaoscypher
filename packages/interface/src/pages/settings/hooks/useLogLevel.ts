// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for the application log level + diagnostic bundle
 * export (LogsTab service-log sub-tab controls).
 *
 * `useLogLevel` reads the current level + the available level list;
 * `useSetLogLevel` changes it and writes the response back into the cache.
 * `useExportDiagnostics` is a side-effecting mutation (triggers a download).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { diagnosticsApi, loggingApi } from '../../../services/api/logs';

interface LoggingLevel {
  level: string;
  available_levels: string[];
}

const LOG_LEVEL_QUERY_KEY = ['settings', 'logging', 'level'] as const;

export function useLogLevel() {
  return useQuery<LoggingLevel>({
    queryKey: LOG_LEVEL_QUERY_KEY,
    queryFn: async () => {
      const res = await loggingApi.getLevel();
      return { level: res.level, available_levels: res.available_levels };
    },
  });
}

export function useSetLogLevel() {
  const qc = useQueryClient();
  return useMutation<string | null, Error, string>({
    mutationFn: async (level) => {
      const res = await loggingApi.setLevel(level);
      return res.success ? res.new_level : null;
    },
    onSuccess: (newLevel) => {
      if (newLevel) {
        qc.setQueryData<LoggingLevel>(LOG_LEVEL_QUERY_KEY, (prev) =>
          prev ? { ...prev, level: newLevel } : prev,
        );
      }
    },
  });
}

export function useExportDiagnostics() {
  return useMutation<void, Error, void>({
    mutationFn: () => diagnosticsApi.exportBundle(),
  });
}
