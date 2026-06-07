// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { apiClient } from './client';
import type { components } from '../../types/generated/api';

export type PublicSettings = components['schemas']['PublicSettings'];

/**
 * Fetch the operator-tunable backend settings.
 *
 * Reachable without auth — this endpoint is auth-exempt at the nginx layer
 * because the SPA needs it to render the login screen.
 *
 * Note: `apiClient` already prefixes `/api/v1`, so the relative path is
 * `/settings/public` — the full URL is `/api/v1/settings/public`.
 */
export async function fetchPublicSettings(): Promise<PublicSettings> {
  const { data } = await apiClient.get<PublicSettings>('/settings/public');
  return data;
}
