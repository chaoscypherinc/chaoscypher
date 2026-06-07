// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Client for the /api/v1/upgrade endpoints.
 *
 * Surfaces pending migrations, applies them, or rolls back to the
 * pre-upgrade backup. Used by the maintenance-mode page and the
 * top-level 503 detector in App.tsx.
 */

import { apiClient } from './client';

export type MigrationTier = 'safe_auto' | 'needs_confirmation' | 'manual';

export interface PendingMigration {
  revision: string;
  tier: MigrationTier;
  description: string;
}

export interface PendingUpgradesResponse {
  ready: boolean;
  blocked_on: PendingMigration[];
  message: string;
  last_backup: string | null;
  last_applied: string[];
  data_changing: boolean;
}

interface ApplyResponse {
  applied: string[];
  current_revision: string | null;
  backup_path: string | null;
}

interface RollbackResponse {
  restored_from: string;
  revision: string | null;
}

const BASE = '/upgrade';

export async function fetchPendingUpgrades(): Promise<PendingUpgradesResponse> {
  const res = await apiClient.get<PendingUpgradesResponse>(`${BASE}/pending`);
  return res.data;
}

export async function applyUpgrades(): Promise<ApplyResponse> {
  const res = await apiClient.post<ApplyResponse>(`${BASE}/apply`);
  return res.data;
}

export async function rollbackUpgrade(): Promise<RollbackResponse> {
  const res = await apiClient.post<RollbackResponse>(`${BASE}/rollback`);
  return res.data;
}
