// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for `useUploadDialogState` — the app-shell (Layout) upload entry point.
 *
 * The decisive assertion for the wizard feature: this entry point exposes its
 * own upload wizard (via `uploadHook.wizard`). The Sources page owns a separate
 * independent instance — only the entry point whose `handleUploadConfirm` ran
 * goes non-idle; the other stays idle and renders nothing. Both surfaces can
 * open the wizard. (The wizard's open behavior is exercised in
 * useSourcesUpload.test.tsx; here we confirm the app-shell seam surfaces it.)
 */

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { installApiClientMock } from '../../test/mocks/apiClient';

vi.mock('../../services/api/client', () => installApiClientMock());
vi.mock('../../contexts/useNotification', () => ({
  useNotification: () => ({ notify: vi.fn() }),
}));
vi.mock('../../contexts/useAppConfig', () => ({
  useAppConfig: () => ({ batch_max_upload_bytes: 5 * 1024 * 1024 * 1024 }),
}));

import { useUploadDialogState } from '../useUploadDialogState';

function wrap({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MemoryRouter>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

describe('useUploadDialogState (app-shell entry point)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('exposes the shared upload wizard (idle by default)', () => {
    const { result } = renderHook(() => useUploadDialogState(), { wrapper: wrap });
    expect(result.current.uploadHook.wizard).toBeDefined();
    expect(result.current.uploadHook.wizard.phase).toBe('idle');
    expect(typeof result.current.uploadHook.wizard.start).toBe('function');
  });

  it('opens and closes the upload dialog (the wizard front door)', () => {
    const { result } = renderHook(() => useUploadDialogState(), { wrapper: wrap });
    expect(result.current.uploadDialogOpen).toBe(false);

    act(() => result.current.openUploadDialog());
    expect(result.current.uploadDialogOpen).toBe(true);

    act(() => result.current.closeUploadDialog());
    expect(result.current.uploadDialogOpen).toBe(false);
  });
});
