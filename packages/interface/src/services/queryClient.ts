// SPDX-License-Identifier: AGPL-3.0-only
// SPDX-FileCopyrightText: 2026 Denis MacPherson

import { QueryClient } from "@tanstack/react-query";

import { DEFAULT_PUBLIC_SETTINGS } from "../contexts/publicSettingsContextValue";
import { isApiError } from "./api/client";

/**
 * Retry a query at most once, and only when a retry can plausibly succeed.
 * Client errors (4xx) are deterministic — re-issuing them doubles the
 * time-to-error and, on 401, runs the login redirect a second time
 * mid-navigation. Network failures (status null) and 5xx stay retryable.
 */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (failureCount >= 1) return false;
  if (isApiError(error) && error.status !== null && error.status < 500) return false;
  return true;
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: DEFAULT_PUBLIC_SETTINGS.cache_default_stale_time_ms,
      gcTime: DEFAULT_PUBLIC_SETTINGS.cache_default_gc_time_ms,
      retry: shouldRetryQuery,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
});
