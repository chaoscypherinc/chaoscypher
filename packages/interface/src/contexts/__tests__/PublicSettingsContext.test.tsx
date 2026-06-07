// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';

import * as api from '../../services/api/publicSettings';
import { PublicSettingsProvider } from '../PublicSettingsContext';
import { useAppConfig } from '../useAppConfig';
import { DEFAULT_PUBLIC_SETTINGS } from '../publicSettingsContextValue';

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <PublicSettingsProvider>{children}</PublicSettingsProvider>
    </QueryClientProvider>
  );
}

describe('useAppConfig', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('returns DEFAULT_PUBLIC_SETTINGS while loading', () => {
    vi.spyOn(api, 'fetchPublicSettings').mockImplementation(() => new Promise(() => {}));
    const { result } = renderHook(() => useAppConfig(), { wrapper: makeWrapper() });
    expect(result.current).toEqual(DEFAULT_PUBLIC_SETTINGS);
  });

  it('returns server values once fetched', async () => {
    const server = { ...DEFAULT_PUBLIC_SETTINGS, pagination_default_page_size: 77 };
    vi.spyOn(api, 'fetchPublicSettings').mockResolvedValue(server);
    const { result } = renderHook(() => useAppConfig(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.pagination_default_page_size).toBe(77));
  });

  it('falls back to defaults if the fetch fails', async () => {
    vi.spyOn(api, 'fetchPublicSettings').mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => useAppConfig(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current).toEqual(DEFAULT_PUBLIC_SETTINGS));
  });
});
