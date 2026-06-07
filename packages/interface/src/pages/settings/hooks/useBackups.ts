// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for database backups (BackupTab).
 *
 * `useBackups` reads the backup list; create / restore / delete are mutations
 * that invalidate the list on success. Download is a side-effecting mutation
 * (triggers a browser download) and does not touch the cache.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { backupApi } from '../../../services/api/backup';
import type { BackupInfo } from '../../../services/api/backup';

interface RestoreResult {
  database: string;
  restored_from: string;
}

const BACKUPS_QUERY_KEY = ['settings', 'backups'] as const;

export function useBackups() {
  return useQuery<BackupInfo[]>({
    queryKey: BACKUPS_QUERY_KEY,
    queryFn: () => backupApi.list(),
  });
}

export function useCreateBackup() {
  const qc = useQueryClient();
  return useMutation<BackupInfo, Error, void>({
    mutationFn: () => backupApi.create(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: BACKUPS_QUERY_KEY });
    },
  });
}

export function useRestoreBackup() {
  const qc = useQueryClient();
  return useMutation<RestoreResult, Error, string>({
    mutationFn: (filename) => backupApi.restore(filename),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: BACKUPS_QUERY_KEY });
    },
  });
}

export function useDeleteBackup() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (filename) => backupApi.delete(filename),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: BACKUPS_QUERY_KEY });
    },
  });
}

export function useDownloadBackup() {
  return useMutation<void, Error, string>({
    mutationFn: (filename) => backupApi.download(filename),
  });
}
