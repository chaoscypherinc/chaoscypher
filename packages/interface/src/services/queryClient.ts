// SPDX-License-Identifier: AGPL-3.0-only
// SPDX-FileCopyrightText: 2026 Denis MacPherson

import { QueryClient } from "@tanstack/react-query";

import { DEFAULT_PUBLIC_SETTINGS } from "../contexts/publicSettingsContextValue";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: DEFAULT_PUBLIC_SETTINGS.cache_default_stale_time_ms,
      gcTime: DEFAULT_PUBLIC_SETTINGS.cache_default_gc_time_ms,
      retry: 1,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
});
