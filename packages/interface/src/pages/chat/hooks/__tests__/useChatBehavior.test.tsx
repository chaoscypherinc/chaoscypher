// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Behavior-pinning suite for the chat hooks.
 *
 * Written first (test gate) to lock the user-visible behavior of the chat
 * stack so the TanStack Query rearchitecture can be verified to preserve it
 * exactly. Drives the `useChat` orchestrator through `renderHook` with the
 * real provider stack (Router + QueryClient + Settings), mocking:
 *   - CRUD / history / scope at the `apiClient` layer (`installApiClientMock`)
 *   - the SSE transport at `globalThis.fetch` (`installSseFetchMock`), the
 *     exact boundary `useChatStream` consumes.
 *
 * These tests are intentionally agnostic to whether chat state lives in
 * `useState` or the Query cache — they assert the observable `useChat` return
 * surface, so they stay green across the rearchitecture.
 */

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import { ThemeProvider, createTheme } from '@mui/material';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import {
  installSseFetchMock,
  makeSseResponse,
  makeControlledSseResponse,
  type SseEvent,
} from '../../../../test/mocks/sseStream';
import { apiClient } from '../../../../services/api/client';
import { useChat } from '../useChat';
import type { Chat, ChatMessage, ChatMetadata } from '../../../../types';

vi.mock('../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<typeof installApiClientMock>['apiClient'];

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeChatMeta(overrides: Partial<ChatMetadata> = {}): ChatMetadata {
  return {
    id: 'c1',
    title: 'First chat',
    status: 'completed',
    message_count: 0,
    source_ids: null,
    created_at: '2026-05-25T00:00:00Z',
    updated_at: '2026-05-25T00:00:00Z',
    ...overrides,
  };
}

function makeChat(overrides: Partial<Chat> = {}): Chat {
  return {
    id: 'c1',
    title: 'First chat',
    status: 'completed',
    messages: [],
    message_count: 0,
    source_ids: null,
    created_at: '2026-05-25T00:00:00Z',
    updated_at: '2026-05-25T00:00:00Z',
    ...overrides,
  };
}

/** Paginated envelope `chatApi.listChats` unwraps. */
function listEnvelope(chats: ChatMetadata[]) {
  return {
    data: { data: chats, pagination: { page: 1, page_size: 50, total_items: chats.length, total_pages: 1 } },
  };
}

/**
 * Configure the apiClient GET mock to route `/chats` (list) and
 * `/chats/:id` (history) responses. `chats` is the sidebar list; `chat`
 * resolves a single chat (or a per-id map for multi-chat tests).
 */
function routeGet(opts: {
  chats?: ChatMetadata[];
  chat?: Chat | ((id: string) => Chat);
}) {
  mockedApiClient.get.mockImplementation((url: string) => {
    if (url === '/chats') {
      return Promise.resolve(listEnvelope(opts.chats ?? []));
    }
    // /chats/:id  (history); avoid matching /chats/:id/export here
    const m = url.match(/^\/chats\/([^/]+)$/);
    if (m) {
      const id = m[1];
      const resolved = typeof opts.chat === 'function' ? opts.chat(id) : (opts.chat ?? makeChat({ id }));
      return Promise.resolve({ data: resolved });
    }
    const exp = url.match(/^\/chats\/([^/]+)\/export$/);
    if (exp) {
      return Promise.resolve({ data: { data: makeChat({ id: exp[1] }) } });
    }
    return Promise.resolve({ data: {} });
  });
}

// Captures the current router location so tests can assert navigation.
let lastPath = '';
function LocationProbe() {
  const loc = useLocation();
  lastPath = loc.pathname;
  return null;
}

function makeChatWrapper(initialEntries: string[]): React.FC<{ children: ReactNode }> {
  const theme = createTheme({ palette: { mode: 'dark' } });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return function Wrapper({ children }) {
    return (
      <MemoryRouter initialEntries={initialEntries}>
        <ThemeProvider theme={theme}>
          <QueryClientProvider client={queryClient}>
            <LocationProbe />
            <Routes>
              <Route path="/chat" element={<>{children}</>} />
              <Route path="/chat/:chatId" element={<>{children}</>} />
            </Routes>
          </QueryClientProvider>
        </ThemeProvider>
      </MemoryRouter>
    );
  };
}

/** Find the last assistant message content in the orchestrator's message list. */
function lastAssistant(messages: ChatMessage[]): ChatMessage | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'assistant') return messages[i];
  }
  return undefined;
}

beforeEach(() => {
  lastPath = '';
  vi.clearAllMocks();
  // Sensible defaults; individual tests override.
  routeGet({ chats: [] });
  mockedApiClient.post.mockResolvedValue({ data: {} });
  mockedApiClient.patch.mockResolvedValue({ data: {} });
  mockedApiClient.delete.mockResolvedValue({ data: {} });
});

// ===========================================================================
// 1. Load a chat by id → history renders
// ===========================================================================

describe('chat: load history by id', () => {
  it('loads the chat referenced by the URL and renders its messages', async () => {
    installSseFetchMock(() => makeSseResponse([]));
    const history: ChatMessage[] = [
      { role: 'user', content: 'Hello there' },
      { role: 'assistant', content: 'General Kenobi' },
    ];
    routeGet({
      chats: [makeChatMeta({ id: 'c1', message_count: 2 })],
      chat: makeChat({ id: 'c1', messages: history, message_count: 2 }),
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });

    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));
    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(result.current.messages[0]).toMatchObject({ role: 'user', content: 'Hello there' });
    expect(result.current.messages[1]).toMatchObject({ role: 'assistant', content: 'General Kenobi' });
  });
});

// ===========================================================================
// 2. Send a message → optimistic user msg + assistant placeholder, tokens
//    accumulate into the rendered assistant message
// ===========================================================================

describe('chat: send + streaming accumulation', () => {
  it('shows optimistic user + placeholder, then accumulates streamed tokens', async () => {
    const streamEvents: SseEvent[] = [
      { type: 'content', accumulated: 'Hel' },
      { type: 'content', accumulated: 'Hello' },
      { type: 'content', accumulated: 'Hello world' },
      { type: 'done', content: 'Hello world' },
    ];
    installSseFetchMock(() => makeSseResponse(streamEvents));
    routeGet({
      chats: [makeChatMeta({ id: 'c1' })],
      chat: makeChat({ id: 'c1' }),
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });

    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    await act(async () => {
      await result.current.handleSend('What is up?');
    });

    // User message present
    await waitFor(() =>
      expect(result.current.messages.some((m) => m.role === 'user' && m.content === 'What is up?')).toBe(true),
    );
    // Streamed tokens accumulated into the assistant message
    await waitFor(() => expect(lastAssistant(result.current.messages)?.content).toBe('Hello world'));
    // Streaming finished → loading cleared
    await waitFor(() => expect(result.current.loading).toBe(false));
  });

  it('clears loading when the SSE events endpoint responds non-OK (no stuck spinner)', async () => {
    // A 502 from /events makes response.ok false → the non-OK fallback branch
    // runs. It must clear loading so the input is usable again; otherwise the
    // user is stranded on an infinite spinner with no in-app recovery.
    installSseFetchMock(() => new Response(null, { status: 502 }));
    routeGet({ chats: [makeChatMeta({ id: 'c1' })], chat: makeChat({ id: 'c1' }) });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    await act(async () => {
      await result.current.handleSend('hi there');
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
  });

  it('leaves the bubble alone on a contentless done (poll delivers the answer)', async () => {
    // Contentless done = subscribe race; the persisted answer arrives via
    // polling. Overwriting with a fake apology was the 2026-06-10 audit bug.
    installSseFetchMock(() => makeSseResponse([{ type: 'done' }]));
    routeGet({ chats: [makeChatMeta({ id: 'c1' })], chat: makeChat({ id: 'c1' }) });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    await act(async () => {
      await result.current.handleSend('hi');
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(lastAssistant(result.current.messages)?.content ?? '').not.toMatch(
      /encountered an issue/i,
    );
  });
});

// ===========================================================================
// 3. New chat created mid-send → lands in sidebar list + URL navigates,
//    in-flight stream not lost
// ===========================================================================

describe('chat: new chat created mid-send', () => {
  it('creates a chat, navigates, keeps the streamed reply, and lists it', async () => {
    // Stream emits content then done.
    installSseFetchMock(() =>
      makeSseResponse([
        { type: 'content', accumulated: 'Streamed answer' },
        { type: 'done', content: 'Streamed answer' },
      ]),
    );

    // No chat initially; after creation the list returns the new chat. Any
    // GET of the new chat (post-creation reload / polling) returns what the
    // worker persisted for the turn, so a reload can't silently wipe the
    // streamed reply — that persistence is the backend's source of truth.
    let listAfterCreate = false;
    const persistedNew1 = makeChat({
      id: 'new-1',
      title: 'New Chat',
      messages: [
        { role: 'user', content: 'Make me a new chat' },
        { role: 'assistant', content: 'Streamed answer' },
      ],
      message_count: 2,
    });
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/chats') {
        return Promise.resolve(
          listEnvelope(listAfterCreate ? [makeChatMeta({ id: 'new-1', title: 'New Chat' })] : []),
        );
      }
      if (url === '/chats/new-1') return Promise.resolve({ data: persistedNew1 });
      const m = url.match(/^\/chats\/([^/]+)$/);
      if (m) return Promise.resolve({ data: makeChat({ id: m[1] }) });
      return Promise.resolve({ data: {} });
    });

    // createChat POST → /chats returns the freshly created chat.
    mockedApiClient.post.mockImplementation((url: string) => {
      if (url === '/chats') {
        listAfterCreate = true;
        return Promise.resolve({ data: makeChat({ id: 'new-1', title: 'New Chat' }) });
      }
      // /chats/:id/send and /chats/:id/generate_title
      if (url.endsWith('/generate_title')) {
        return Promise.resolve({ data: { title: 'New Chat' } });
      }
      return Promise.resolve({ data: { task_id: 't1', status: 'processing' } });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat']), // no id → new chat
    });

    await waitFor(() => expect(result.current.currentChat).toBeNull());

    await act(async () => {
      await result.current.handleSend('Make me a new chat');
    });

    // URL navigated to the created chat
    await waitFor(() => expect(lastPath).toBe('/chat/new-1'));
    // The created chat is current
    await waitFor(() => expect(result.current.currentChat?.id).toBe('new-1'));
    // Streamed reply preserved (not lost across navigation)
    await waitFor(() => expect(lastAssistant(result.current.messages)?.content).toBe('Streamed answer'));
    // User message preserved
    expect(result.current.messages.some((m) => m.role === 'user' && m.content === 'Make me a new chat')).toBe(true);
    // The new chat lands in the sidebar list
    await waitFor(() => expect(result.current.chats.some((c) => c.id === 'new-1')).toBe(true));
  });
});

// ===========================================================================
// 4. Title-autogen event updates the sidebar list
// ===========================================================================

describe('chat: title autogen', () => {
  it('updates the sidebar list title after a new-chat stream completes', async () => {
    installSseFetchMock(() =>
      makeSseResponse([
        { type: 'content', accumulated: 'ok' },
        { type: 'done', content: 'ok' },
      ]),
    );

    let listTitle = 'New Chat';
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/chats') {
        return Promise.resolve(listEnvelope([makeChatMeta({ id: 'new-1', title: listTitle })]));
      }
      const m = url.match(/^\/chats\/([^/]+)$/);
      if (m) return Promise.resolve({ data: makeChat({ id: m[1], title: listTitle }) });
      return Promise.resolve({ data: {} });
    });

    mockedApiClient.post.mockImplementation((url: string) => {
      if (url === '/chats') {
        return Promise.resolve({ data: makeChat({ id: 'new-1', title: 'New Chat' }) });
      }
      if (url.endsWith('/generate_title')) {
        listTitle = 'Auto Generated Title';
        return Promise.resolve({ data: { title: 'Auto Generated Title' } });
      }
      return Promise.resolve({ data: { task_id: 't1', status: 'processing' } });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat']),
    });
    await waitFor(() => expect(result.current.currentChat).toBeNull());

    await act(async () => {
      await result.current.handleSend('hello');
    });

    await waitFor(() =>
      expect(result.current.chats.find((c) => c.id === 'new-1')?.title).toBe('Auto Generated Title'),
    );
  });
});

// ===========================================================================
// 5. Tool-approval round-trip
// ===========================================================================

describe('chat: tool approval round-trip', () => {
  it('surfaces a pending approval, then clears it on approve (decideTool)', async () => {
    const controlled = makeControlledSseResponse();
    installSseFetchMock(() => controlled.response);
    routeGet({ chats: [makeChatMeta({ id: 'c1' })], chat: makeChat({ id: 'c1' }) });

    // decideTool POST → 204 success
    mockedApiClient.post.mockResolvedValue({ data: undefined, status: 204 });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    // Kick off the send; the controlled stream stays open.
    let sendPromise: Promise<void>;
    act(() => {
      sendPromise = result.current.handleSend('use a tool');
    });

    // Backend emits an approval-required event.
    act(() => {
      controlled.push({
        type: 'tool_approval_required',
        tool_call_id: 'tc-1',
        tool_name: 'create_node',
        arguments: { label: 'X' },
        iteration: 1,
      });
    });

    await waitFor(() => expect(result.current.pendingApproval?.tool_call_id).toBe('tc-1'));

    // User approves → POST decision; pending clears.
    await act(async () => {
      await result.current.decideToolApproval('approve');
    });
    await waitFor(() => expect(result.current.pendingApproval).toBeNull());

    // Stream resumes + completes.
    act(() => {
      controlled.push({ type: 'content', accumulated: 'done after tool' });
      controlled.push({ type: 'done', content: 'done after tool' });
      controlled.close();
    });

    await act(async () => {
      await sendPromise!;
    });
    await waitFor(() => expect(lastAssistant(result.current.messages)?.content).toBe('done after tool'));
  });

  it('clears stale pending approval when decideTool returns 404', async () => {
    const controlled = makeControlledSseResponse();
    installSseFetchMock(() => controlled.response);
    routeGet({ chats: [makeChatMeta({ id: 'c1' })], chat: makeChat({ id: 'c1' }) });

    // Only the tool_decision POST rejects with a 404 ApiClientError-shaped
    // object; the `send` POST that kicks off the turn must still succeed.
    mockedApiClient.post.mockImplementation((url: string) => {
      if (url.endsWith('/tool_decision')) {
        return Promise.reject({ isApiError: true, status: 404, message: 'not found' });
      }
      return Promise.resolve({ data: { task_id: 't1', status: 'processing' } });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    act(() => {
      void result.current.handleSend('use a tool');
    });
    act(() => {
      controlled.push({
        type: 'tool_approval_required',
        tool_call_id: 'tc-404',
        tool_name: 'delete_node',
        arguments: {},
        iteration: 1,
      });
    });
    await waitFor(() => expect(result.current.pendingApproval?.tool_call_id).toBe('tc-404'));

    // 404 path swallows the error and clears local state (no throw).
    await act(async () => {
      await result.current.decideToolApproval('approve');
    });
    await waitFor(() => expect(result.current.pendingApproval).toBeNull());

    act(() => controlled.close());
  });
});

// ===========================================================================
// 6. Scope update / clear
// ===========================================================================

describe('chat: scope update/clear', () => {
  it('updates scope and reflects the new source_ids on the current chat', async () => {
    installSseFetchMock(() => makeSseResponse([]));
    routeGet({ chats: [makeChatMeta({ id: 'c1' })], chat: makeChat({ id: 'c1' }) });

    // PATCH /chats/c1/scope → returns chat with new source_ids
    mockedApiClient.patch.mockImplementation((url: string) => {
      if (url === '/chats/c1/scope') {
        return Promise.resolve({ data: makeChat({ id: 'c1', source_ids: ['s1', 's2'] }) });
      }
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    await act(async () => {
      await result.current.handleUpdateScope(['s1', 's2']);
    });

    await waitFor(() => expect(result.current.currentChat?.source_ids).toEqual(['s1', 's2']));
  });

  it('clears scope and reflects the empty source_ids', async () => {
    installSseFetchMock(() => makeSseResponse([]));
    routeGet({
      chats: [makeChatMeta({ id: 'c1', source_ids: ['s1'] })],
      chat: makeChat({ id: 'c1', source_ids: ['s1'] }),
    });

    mockedApiClient.delete.mockImplementation((url: string) => {
      if (url === '/chats/c1/scope') {
        return Promise.resolve({ data: makeChat({ id: 'c1', source_ids: [] }) });
      }
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.source_ids).toEqual(['s1']));

    await act(async () => {
      await result.current.handleClearScope();
    });

    await waitFor(() => expect(result.current.currentChat?.source_ids).toEqual([]));
  });
});

// ===========================================================================
// 7. Sidebar CRUD: rename / delete / clear-all (optimistic + rollback)
// ===========================================================================

describe('chat: sidebar CRUD', () => {
  it('renames a chat in the sidebar list', async () => {
    installSseFetchMock(() => makeSseResponse([]));
    // The server reflects the rename; the optimistic update + the
    // post-settle invalidate/refetch should both land on 'New title'.
    let serverTitle = 'Old title';
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/chats') {
        return Promise.resolve(listEnvelope([makeChatMeta({ id: 'c1', title: serverTitle })]));
      }
      const m = url.match(/^\/chats\/([^/]+)$/);
      if (m) return Promise.resolve({ data: makeChat({ id: m[1], title: serverTitle }) });
      return Promise.resolve({ data: {} });
    });
    mockedApiClient.patch.mockImplementation((url: string) => {
      if (url === '/chats/c1') {
        serverTitle = 'New title';
        return Promise.resolve({ data: makeChat({ id: 'c1', title: 'New title' }) });
      }
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.chats.find((c) => c.id === 'c1')?.title).toBe('Old title'));

    await act(async () => {
      await result.current.handleRenameChat('c1', 'New title');
    });

    await waitFor(() => expect(result.current.chats.find((c) => c.id === 'c1')?.title).toBe('New title'));
  });

  it('deletes a chat from the sidebar list and navigates away when current', async () => {
    installSseFetchMock(() => makeSseResponse([]));
    let deleted = false;
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/chats') {
        return Promise.resolve(listEnvelope(deleted ? [] : [makeChatMeta({ id: 'c1' })]));
      }
      const m = url.match(/^\/chats\/([^/]+)$/);
      if (m) return Promise.resolve({ data: makeChat({ id: m[1] }) });
      return Promise.resolve({ data: {} });
    });
    mockedApiClient.delete.mockImplementation((url: string) => {
      if (url === '/chats/c1') {
        deleted = true;
        return Promise.resolve({ data: { success: true } });
      }
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.chats.some((c) => c.id === 'c1')).toBe(true));

    await act(async () => {
      await result.current.handleDeleteChat('c1');
    });

    await waitFor(() => expect(result.current.chats.some((c) => c.id === 'c1')).toBe(false));
    await waitFor(() => expect(lastPath).toBe('/chat'));
  });

  it('rolls back the optimistic delete when the API call fails', async () => {
    installSseFetchMock(() => makeSseResponse([]));
    routeGet({ chats: [makeChatMeta({ id: 'c1', title: 'Keep me' })], chat: makeChat({ id: 'c1' }) });
    mockedApiClient.delete.mockRejectedValue(new Error('delete boom'));

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.chats.some((c) => c.id === 'c1')).toBe(true));

    await act(async () => {
      await result.current.handleDeleteChat('c1');
    });

    // After failure, the chat is still present (rolled back / re-synced).
    await waitFor(() => expect(result.current.chats.some((c) => c.id === 'c1')).toBe(true));
  });

  it('clears all chats from the sidebar list', async () => {
    installSseFetchMock(() => makeSseResponse([]));
    let cleared = false;
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/chats') {
        return Promise.resolve(
          listEnvelope(cleared ? [] : [makeChatMeta({ id: 'c1' }), makeChatMeta({ id: 'c2' })]),
        );
      }
      const m = url.match(/^\/chats\/([^/]+)$/);
      if (m) return Promise.resolve({ data: makeChat({ id: m[1] }) });
      return Promise.resolve({ data: {} });
    });
    mockedApiClient.delete.mockImplementation((url: string) => {
      if (url === '/chats') {
        cleared = true;
        return Promise.resolve({ data: {} });
      }
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat']),
    });
    await waitFor(() => expect(result.current.chats).toHaveLength(2));

    await act(async () => {
      await result.current.handleClearAllChats();
    });

    await waitFor(() => expect(result.current.chats).toHaveLength(0));
    await waitFor(() => expect(lastPath).toBe('/chat'));
  });
});

// ===========================================================================
// 8. Abort on unmount / new stream replacing old
// ===========================================================================

describe('chat: abort handling', () => {
  it('aborts the in-flight stream on unmount without throwing', async () => {
    const controlled = makeControlledSseResponse();
    let capturedSignal: AbortSignal | undefined;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/events')) {
        capturedSignal = init?.signal ?? undefined;
        return Promise.resolve(controlled.response);
      }
      return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
    });
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    try {
      routeGet({ chats: [makeChatMeta({ id: 'c1' })], chat: makeChat({ id: 'c1' }) });
      mockedApiClient.post.mockResolvedValue({ data: { task_id: 't', status: 'processing' } });

      const { result, unmount } = renderHook(() => useChat(), {
        wrapper: makeChatWrapper(['/chat/c1']),
      });
      await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

      act(() => {
        void result.current.handleSend('start a long stream');
      });
      // Stream opened
      await waitFor(() => expect(capturedSignal).toBeDefined());
      expect(capturedSignal?.aborted).toBe(false);

      // Unmount → abort fires
      unmount();
      expect(capturedSignal?.aborted).toBe(true);

      controlled.close();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('does not start a second stream while one is in flight (loading guard)', async () => {
    // The `loading` guard in handleSend prevents a concurrent second send;
    // a single in-flight stream is the invariant. (The abort-controller in
    // useChatStream still aborts the prior connection should streamEvents be
    // re-invoked — exercised by the unmount test above.)
    const signals: AbortSignal[] = [];
    const controlled = makeControlledSseResponse();
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/events')) {
        if (init?.signal) signals.push(init.signal);
        return Promise.resolve(controlled.response);
      }
      return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
    });
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    try {
      routeGet({ chats: [makeChatMeta({ id: 'c1' })], chat: makeChat({ id: 'c1' }) });
      mockedApiClient.post.mockResolvedValue({ data: { task_id: 't', status: 'processing' } });

      const { result } = renderHook(() => useChat(), {
        wrapper: makeChatWrapper(['/chat/c1']),
      });
      await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

      act(() => {
        void result.current.handleSend('first');
      });
      await waitFor(() => expect(signals.length).toBe(1));
      await waitFor(() => expect(result.current.loading).toBe(true));

      // Second send while loading → guarded no-op, no new stream opened.
      await act(async () => {
        await result.current.handleSend('second');
      });
      expect(signals.length).toBe(1);

      controlled.close();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

// ===========================================================================
// 9. Stop / cancel the in-flight turn (Phase 2)
// ===========================================================================

describe('chat: stop/cancel', () => {
  it('handleStop posts to /cancel and stays "stopping" until done lands', async () => {
    const controlled = makeControlledSseResponse();
    installSseFetchMock(() => controlled.response);
    routeGet({ chats: [makeChatMeta({ id: 'c1' })], chat: makeChat({ id: 'c1' }) });

    const posted: string[] = [];
    mockedApiClient.post.mockImplementation((url: string) => {
      posted.push(url);
      if (url.endsWith('/cancel')) {
        return Promise.resolve({ data: { status: 'cancelling' }, status: 202 });
      }
      return Promise.resolve({ data: { task_id: 't1', status: 'processing' } });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    act(() => {
      void result.current.handleSend('long multi-tool turn');
    });
    await waitFor(() => expect(result.current.loading).toBe(true));

    await act(async () => {
      await result.current.handleStop();
    });
    expect(posted).toContain('/chats/c1/cancel');
    // The spinner is NOT optimistically ended — the worker's done event owns
    // the end of the turn; until then the Stop button is disarmed.
    expect(result.current.stopping).toBe(true);
    expect(result.current.loading).toBe(true);

    // Worker lands at the step boundary and publishes the cancelled done.
    act(() => {
      controlled.push({ type: 'done', status: 'cancelled', content: 'Partial answer' });
      controlled.close();
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(result.current.stopping).toBe(false));
  });

  it('handleStop is a no-op when no turn is in flight', async () => {
    routeGet({ chats: [makeChatMeta({ id: 'c1' })], chat: makeChat({ id: 'c1' }) });
    mockedApiClient.post.mockResolvedValue({ data: {} });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    await act(async () => {
      await result.current.handleStop();
    });
    expect(mockedApiClient.post).not.toHaveBeenCalled();
    expect(result.current.stopping).toBe(false);
  });

  it('handleStop swallows 409 (turn already ended) without re-arming', async () => {
    const controlled = makeControlledSseResponse();
    installSseFetchMock(() => controlled.response);
    routeGet({ chats: [makeChatMeta({ id: 'c1' })], chat: makeChat({ id: 'c1' }) });

    mockedApiClient.post.mockImplementation((url: string) => {
      if (url.endsWith('/cancel')) {
        return Promise.reject({ isApiError: true, status: 409, message: 'No turn in progress' });
      }
      return Promise.resolve({ data: { task_id: 't1', status: 'processing' } });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    act(() => {
      void result.current.handleSend('turn that finished already');
    });
    await waitFor(() => expect(result.current.loading).toBe(true));

    // Must not throw; stopping stays armed (the done event will end it).
    await act(async () => {
      await result.current.handleStop();
    });
    expect(result.current.stopping).toBe(true);

    act(() => {
      controlled.push({ type: 'done', content: 'finished normally' });
      controlled.close();
    });
    await waitFor(() => expect(result.current.stopping).toBe(false));
  });
});

// ===========================================================================
// 10. Refresh-resilience: reattach the stream to processing chats (Phase 3a)
// ===========================================================================

describe('chat: resubscribe on processing chats', () => {
  it('reattaches the live stream when a loaded chat is still processing', async () => {
    const controlled = makeControlledSseResponse();
    const eventsFetches: string[] = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/events')) {
        eventsFetches.push(url);
        return Promise.resolve(controlled.response);
      }
      return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
    });
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    try {
      // The chat loads ALREADY processing (e.g. a page refresh mid-turn).
      routeGet({
        chats: [makeChatMeta({ id: 'c1', status: 'processing' })],
        chat: makeChat({ id: 'c1', status: 'processing' }),
      });
      mockedApiClient.post.mockResolvedValue({ data: {} });

      const { result } = renderHook(() => useChat(), {
        wrapper: makeChatWrapper(['/chat/c1']),
      });
      await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

      // No handleSend happened — the hook must reattach on its own.
      await waitFor(() => expect(eventsFetches.length).toBe(1));
      await waitFor(() => expect(result.current.loading).toBe(true));

      // The background turn finishes; the reattached stream delivers it.
      act(() => {
        controlled.push({ type: 'content', accumulated: 'recovered answer' });
        controlled.push({ type: 'done', content: 'recovered answer' });
        controlled.close();
      });
      await waitFor(() => expect(result.current.loading).toBe(false));
      await waitFor(() =>
        expect(lastAssistant(result.current.messages)?.content).toBe('recovered answer'),
      );
      // No duplicate subscriptions were opened.
      expect(eventsFetches.length).toBe(1);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('does not touch the stream for chats that are not processing', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    try {
      routeGet({ chats: [makeChatMeta({ id: 'c1' })], chat: makeChat({ id: 'c1' }) });
      const { result } = renderHook(() => useChat(), {
        wrapper: makeChatWrapper(['/chat/c1']),
      });
      await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));
      expect(result.current.loading).toBe(false);
      const eventsCalls = fetchMock.mock.calls.filter((c) => String(c[0]).includes('/events'));
      expect(eventsCalls.length).toBe(0);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

// ===========================================================================
// 11. Cross-chat isolation: chat A's stream flags never show on chat B
// ===========================================================================

describe('chat: cross-chat stream isolation', () => {
  it("chat A's spinner does not bleed onto chat B and re-arms on return", async () => {
    const controlled = makeControlledSseResponse();
    installSseFetchMock(() => controlled.response);
    routeGet({
      chats: [makeChatMeta({ id: 'c1', status: 'processing' }), makeChatMeta({ id: 'c2' })],
      chat: (id) => makeChat({ id, status: id === 'c1' ? 'processing' : 'completed' }),
    });
    mockedApiClient.post.mockResolvedValue({ data: {} });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    // c1 is processing — the hook reattaches and shows the spinner.
    await waitFor(() => expect(result.current.loading).toBe(true));

    // Switching to idle chat B must not show A's spinner.
    act(() => {
      result.current.handleSelectChat('c2');
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c2'));
    expect(result.current.loading).toBe(false);
    expect(result.current.isStreamingActive).toBe(false);

    // Returning to the still-streaming chat shows it again.
    act(() => {
      result.current.handleSelectChat('c1');
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));
    await waitFor(() => expect(result.current.loading).toBe(true));

    act(() => {
      controlled.push({ type: 'done', content: 'finished' });
      controlled.close();
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
  });
});

// ===========================================================================
// 12. done event patches the cached chat status (no redundant poll)
// ===========================================================================

describe('chat: done event patches cached status', () => {
  it("flips the cached chat status to 'active' when done lands", async () => {
    const controlled = makeControlledSseResponse();
    installSseFetchMock(() => controlled.response);
    // The chat starts processing — the cached status would otherwise stay
    // 'processing' (and keep the poller alive) until a refetch resolves.
    routeGet({
      chats: [makeChatMeta({ id: 'c1', status: 'processing' })],
      chat: makeChat({ id: 'c1', status: 'processing' }),
    });
    mockedApiClient.post.mockResolvedValue({ data: {} });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.loading).toBe(true)); // reattached

    act(() => {
      controlled.push({ type: 'done', content: 'all done' });
      controlled.close();
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    // The cache itself was patched — no poll/refetch needed.
    await waitFor(() => expect(result.current.currentChat?.status).toBe('active'));
  });
});

// ===========================================================================
// 13. Retry a failed turn without duplicating the user message
// ===========================================================================

describe('chat: retry after worker error', () => {
  it('handleRetry POSTs /retry (never /send or /messages) and reattaches', async () => {
    const controlled = makeControlledSseResponse();
    installSseFetchMock(() => controlled.response);
    routeGet({
      chats: [makeChatMeta({ id: 'c1', status: 'error' })],
      chat: makeChat({
        id: 'c1',
        status: 'error',
        messages: [
          { id: 'm1', role: 'user', content: 'the question', created_at: '2026-06-10T00:00:00Z' } as ChatMessage,
        ],
      }),
    });

    const posted: string[] = [];
    mockedApiClient.post.mockImplementation((url: string) => {
      posted.push(url);
      return Promise.resolve({ data: { task_id: 't-retry', status: 'processing' } });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    await act(async () => {
      void result.current.handleRetry();
    });
    await waitFor(() => expect(result.current.loading).toBe(true));
    expect(posted).toEqual(['/chats/c1/retry']);

    act(() => {
      controlled.push({ type: 'content', accumulated: 'second attempt answer' });
      controlled.push({ type: 'done', content: 'second attempt answer' });
      controlled.close();
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() =>
      expect(lastAssistant(result.current.messages)?.content).toBe('second attempt answer'),
    );
    // Exactly one POST happened — the user message was NOT re-sent.
    expect(posted).toEqual(['/chats/c1/retry']);
  });

  it('a failed retry surfaces a fresh retryable error', async () => {
    routeGet({
      chats: [makeChatMeta({ id: 'c1', status: 'error' })],
      chat: makeChat({ id: 'c1', status: 'error' }),
    });
    mockedApiClient.post.mockRejectedValue({ isApiError: true, status: 503, message: 'queue down' });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    await act(async () => {
      await result.current.handleRetry();
    });
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.loading).toBe(false);
  });
});

// ===========================================================================
// 14. Regenerate + edit-and-resend (Phase 4a)
// ===========================================================================

describe('chat: regenerate and edit-and-resend', () => {
  it('handleRegenerate POSTs /regenerate, trims the old answer, reattaches', async () => {
    const controlled = makeControlledSseResponse();
    installSseFetchMock(() => controlled.response);
    routeGet({
      chats: [makeChatMeta({ id: 'c1' })],
      chat: makeChat({
        id: 'c1',
        messages: [
          { id: 'm1', role: 'user', content: 'the question' } as ChatMessage,
          { id: 'm2', role: 'assistant', content: 'old answer' } as ChatMessage,
        ],
      }),
    });

    const posted: string[] = [];
    mockedApiClient.post.mockImplementation((url: string) => {
      posted.push(url);
      return Promise.resolve({ data: { task_id: 't-rg', status: 'processing' } });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    await act(async () => {
      void result.current.handleRegenerate();
    });
    await waitFor(() => expect(result.current.loading).toBe(true));
    expect(posted).toEqual(['/chats/c1/regenerate']);
    // Optimistic trim: the stale answer is gone, the user message stays.
    const contents = result.current.messages.map((m) => m.content);
    expect(contents).toContain('the question');
    expect(contents).not.toContain('old answer');

    act(() => {
      controlled.push({ type: 'done', content: 'fresh answer' });
      controlled.close();
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() =>
      expect(lastAssistant(result.current.messages)?.content).toBe('fresh answer'),
    );
  });

  it('edit-and-resend arms the input and sends replace_from_message_id', async () => {
    const controlled = makeControlledSseResponse();
    installSseFetchMock(() => controlled.response);
    routeGet({
      chats: [makeChatMeta({ id: 'c1' })],
      chat: makeChat({
        id: 'c1',
        messages: [
          { id: 'm1', role: 'user', content: 'original question' } as ChatMessage,
          { id: 'm2', role: 'assistant', content: 'old answer' } as ChatMessage,
        ],
      }),
    });

    const bodies: Array<Record<string, unknown>> = [];
    mockedApiClient.post.mockImplementation((url: string, body?: Record<string, unknown>) => {
      if (url.endsWith('/send')) bodies.push(body ?? {});
      return Promise.resolve({ data: { task_id: 't-e', status: 'processing' } });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    act(() => {
      result.current.startEditMessage('m1', 'original question');
    });
    expect(result.current.input).toBe('original question');

    act(() => {
      result.current.setInput('edited question');
    });
    await act(async () => {
      void result.current.handleSend();
    });
    await waitFor(() => expect(bodies.length).toBe(1));
    expect(bodies[0]).toEqual({
      content: 'edited question',
      replace_from_message_id: 'm1',
    });
    // Optimistic replace: the original user message and old answer are gone.
    const contents = result.current.messages.map((m) => m.content);
    expect(contents).toContain('edited question');
    expect(contents).not.toContain('original question');
    expect(contents).not.toContain('old answer');

    act(() => {
      controlled.push({ type: 'done', content: 'answer to the edit' });
      controlled.close();
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
  });

  it('clearing the input disarms a pending edit (plain send, no replace)', async () => {
    const controlled = makeControlledSseResponse();
    installSseFetchMock(() => controlled.response);
    routeGet({
      chats: [makeChatMeta({ id: 'c1' })],
      chat: makeChat({
        id: 'c1',
        messages: [{ id: 'm1', role: 'user', content: 'original' } as ChatMessage],
      }),
    });

    const bodies: Array<Record<string, unknown>> = [];
    mockedApiClient.post.mockImplementation((url: string, body?: Record<string, unknown>) => {
      if (url.endsWith('/send')) bodies.push(body ?? {});
      return Promise.resolve({ data: { task_id: 't', status: 'processing' } });
    });

    const { result } = renderHook(() => useChat(), {
      wrapper: makeChatWrapper(['/chat/c1']),
    });
    await waitFor(() => expect(result.current.currentChat?.id).toBe('c1'));

    act(() => {
      result.current.startEditMessage('m1', 'original');
    });
    act(() => {
      result.current.setInput(''); // user changes their mind
    });
    act(() => {
      result.current.setInput('a brand new question');
    });
    await act(async () => {
      void result.current.handleSend();
    });
    await waitFor(() => expect(bodies.length).toBe(1));
    expect(bodies[0]).toEqual({ content: 'a brand new question' });

    act(() => controlled.close());
  });
});
