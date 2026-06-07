// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Smoke tests for the `useChat` orchestrator's empty-state + new-chat reset.
 *
 * Post-rearchitecture the chat stack reads/writes the TanStack Query cache,
 * so this mocks the `apiClient` layer and provides a `QueryClientProvider`
 * via the shared wrapper. The deeper behavior (streaming, CRUD, scope, tool
 * approval, abort) is pinned in `useChatBehavior.test.tsx`.
 */

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';
import { ThemeProvider, createTheme } from '@mui/material';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { installFetchMock } from '../../../../test/mocks/fetch';
import { apiClient } from '../../../../services/api/client';
import { useChat } from '../useChat';

vi.mock('../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<typeof installApiClientMock>['apiClient'];

function wrapper({ children }: { children: ReactNode }) {
  const theme = createTheme({ palette: { mode: 'dark' } });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MemoryRouter initialEntries={['/chat']}>
      <ThemeProvider theme={theme}>
        <QueryClientProvider client={queryClient}>
          <Routes>
            <Route path="/chat" element={<>{children}</>} />
            <Route path="/chat/:chatId" element={<>{children}</>} />
          </Routes>
        </QueryClientProvider>
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('useChat', () => {
  beforeEach(() => {
    installFetchMock();
    vi.clearAllMocks();
    // listChats unwraps response.data.data → supply a paginated envelope.
    mockedApiClient.get.mockResolvedValue({
      data: { data: [], pagination: { page: 1, page_size: 50, total_items: 0, total_pages: 0 } },
    });
  });

  it('starts with empty chat state', async () => {
    const { result } = renderHook(() => useChat(), { wrapper });

    await waitFor(() => {
      expect(result.current.chats).toEqual([]);
    });
    expect(result.current.currentChat).toBeNull();
    expect(result.current.messages).toEqual([]);
    expect(result.current.input).toBe('');
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('clears state when handleNewChat is called', async () => {
    const { result } = renderHook(() => useChat(), { wrapper });
    await waitFor(() => expect(result.current.chats).toEqual([]));

    act(() => {
      result.current.setInput('draft message');
    });
    expect(result.current.input).toBe('draft message');

    act(() => {
      result.current.handleNewChat();
    });

    await waitFor(() => {
      expect(result.current.currentChat).toBeNull();
      expect(result.current.messages).toEqual([]);
    });
  });
});
