// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useLLMHealth — share the LLM action-gate predicate across the app.
 *
 * Backs the persistent "Configure your LLM" banner, the disabled Import
 * button, and the disabled chat input. The server enforces the same
 * predicate via 409 LLM_NOT_VERIFIED on the import + chat endpoints —
 * the hook is the cosmetic mirror so users see the disabled state
 * before they click.
 */

import { useQuery } from '@tanstack/react-query';
import { settingsApi } from '../services/api/settings';
import type { LLMHealthResponse } from '../types';

const LLM_HEALTH_KEY = ['settings', 'llm', 'health'] as const;

export function useLLMHealth() {
  return useQuery<LLMHealthResponse>({
    queryKey: LLM_HEALTH_KEY,
    queryFn: () => settingsApi.getLLMHealth(),
    // Health changes only when the user clicks Test or edits LLM config —
    // both flows can invalidate the key explicitly. 30s refetch is the
    // safety net for the in-memory-tracker / cortex-restart case.
    staleTime: 30_000,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
}

export { LLM_HEALTH_KEY };
