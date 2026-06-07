// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for chats — the cache-as-source-of-truth layer for
 * the chat feature.
 *
 * Part of the 2026-05-25 chat rearchitecture. The
 * sidebar list lives at `['chats']`; each loaded chat's history lives at
 * `['chat', id]`. CRUD/scope are optimistic mutations that write the cache
 * and invalidate; live SSE streaming writes into `['chat', id]` directly via
 * the cache-writer helpers below (consumed by `useChatStream`).
 *
 * Query keys are intentionally module-local — they are an internal contract
 * of the chat feature and must not leak into a shared key registry.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';

import { chatApi } from './chat';
import type { Chat, ChatMetadata } from '../../types';

// ---------------------------------------------------------------------------
// Query keys (module-local)
// ---------------------------------------------------------------------------

const CHATS_QUERY_KEY = ['chats'] as const;

function chatQueryKey(chatId: string) {
  return ['chat', chatId] as const;
}

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/** Sidebar chat list. */
export function useChats() {
  return useQuery<ChatMetadata[]>({
    queryKey: CHATS_QUERY_KEY,
    queryFn: () => chatApi.listChats(),
  });
}

/**
 * History for a single chat. Disabled when no id is present (the new-chat
 * screen). Streaming writes partial assistant content into this same cache
 * entry, so the query data is the single source of truth for the rendered
 * conversation.
 *
 * Refetch is intentionally conservative: history is fetched on first load
 * (or after `gcTime` eviction) and otherwise kept current by the SSE stream /
 * polling writes. This mirrors the pre-rearchitecture "load on URL change,
 * never silently refetch" semantics and — critically — prevents a background
 * refetch from clobbering live-streamed partials (the race the old
 * `justCreatedChatRef` guard existed to avoid).
 */
export function useChat(chatId: string | null | undefined) {
  return useQuery<Chat>({
    queryKey: chatId ? chatQueryKey(chatId) : ['chat', 'none'],
    queryFn: () => chatApi.getChat(chatId as string),
    enabled: chatId != null,
    staleTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

// ---------------------------------------------------------------------------
// CRUD mutations
// ---------------------------------------------------------------------------

interface RenameVars {
  chatId: string;
  title: string;
}

interface RenameContext {
  previousList: ChatMetadata[] | undefined;
  previousChat: Chat | undefined;
}

/**
 * Rename a chat. Optimistically patches the title in the list and in the
 * `['chat', id]` cache entry, rolls back both on failure, and re-syncs the
 * list on settle.
 */
export function useRenameChat() {
  const qc = useQueryClient();
  return useMutation<Chat, Error, RenameVars, RenameContext>({
    mutationFn: ({ chatId, title }) => chatApi.updateTitle(chatId, title),
    onMutate: async ({ chatId, title }) => {
      await qc.cancelQueries({ queryKey: CHATS_QUERY_KEY });
      const previousList = qc.getQueryData<ChatMetadata[]>(CHATS_QUERY_KEY);
      const previousChat = qc.getQueryData<Chat>(chatQueryKey(chatId));
      qc.setQueryData<ChatMetadata[]>(CHATS_QUERY_KEY, (old) =>
        old?.map((c) => (c.id === chatId ? { ...c, title } : c)) ?? old,
      );
      qc.setQueryData<Chat>(chatQueryKey(chatId), (old) =>
        old ? { ...old, title } : old,
      );
      return { previousList, previousChat };
    },
    onError: (_err, vars, ctx) => {
      if (ctx?.previousList) qc.setQueryData(CHATS_QUERY_KEY, ctx.previousList);
      if (ctx?.previousChat) qc.setQueryData(chatQueryKey(vars.chatId), ctx.previousChat);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: CHATS_QUERY_KEY });
    },
  });
}

interface DeleteContext {
  previousList: ChatMetadata[] | undefined;
}

/**
 * Delete a chat. Optimistically removes it from the list, rolls back on
 * failure, re-syncs on settle.
 */
export function useDeleteChat() {
  const qc = useQueryClient();
  return useMutation<{ success: true }, Error, string, DeleteContext>({
    mutationFn: (chatId) => chatApi.deleteChat(chatId),
    onMutate: async (chatId) => {
      await qc.cancelQueries({ queryKey: CHATS_QUERY_KEY });
      const previousList = qc.getQueryData<ChatMetadata[]>(CHATS_QUERY_KEY);
      qc.setQueryData<ChatMetadata[]>(CHATS_QUERY_KEY, (old) =>
        old?.filter((c) => c.id !== chatId) ?? old,
      );
      return { previousList };
    },
    onError: (_err, _chatId, ctx) => {
      if (ctx?.previousList) qc.setQueryData(CHATS_QUERY_KEY, ctx.previousList);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: CHATS_QUERY_KEY });
    },
  });
}

interface ClearAllContext {
  previousList: ChatMetadata[] | undefined;
}

/** Delete every chat. Optimistically empties the list; rolls back on failure. */
export function useClearAllChats() {
  const qc = useQueryClient();
  return useMutation<void, Error, void, ClearAllContext>({
    mutationFn: () => chatApi.deleteAllChats(),
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: CHATS_QUERY_KEY });
      const previousList = qc.getQueryData<ChatMetadata[]>(CHATS_QUERY_KEY);
      qc.setQueryData<ChatMetadata[]>(CHATS_QUERY_KEY, []);
      return { previousList };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previousList) qc.setQueryData(CHATS_QUERY_KEY, ctx.previousList);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: CHATS_QUERY_KEY });
    },
  });
}

interface CreateChatVars {
  source_ids?: string[];
}

/**
 * Create a chat. Seeds the `['chat', newId]` cache with the returned chat so
 * the history query resolves instantly (the freshly created chat has no
 * messages yet, which streaming then appends to), and invalidates the list so
 * the sidebar picks it up. The caller handles navigation.
 */
export function useCreateChat() {
  const qc = useQueryClient();
  return useMutation<Chat, Error, CreateChatVars>({
    mutationFn: ({ source_ids }) =>
      chatApi.createChat(source_ids && source_ids.length > 0 ? { source_ids } : undefined),
    onSuccess: (chat) => {
      qc.setQueryData<Chat>(chatQueryKey(chat.id), chat);
      void qc.invalidateQueries({ queryKey: CHATS_QUERY_KEY });
    },
  });
}

// ---------------------------------------------------------------------------
// Scope mutations
// ---------------------------------------------------------------------------

interface UpdateScopeVars {
  chatId: string;
  sourceIds: string[];
}

/**
 * Update a chat's source scope. The server returns the full updated chat;
 * we write it into `['chat', id]` (so messages + scope refresh) and
 * invalidate the list (scope can affect list metadata).
 */
export function useUpdateScope() {
  const qc = useQueryClient();
  return useMutation<Chat, Error, UpdateScopeVars>({
    mutationFn: ({ chatId, sourceIds }) => chatApi.updateScope(chatId, sourceIds),
    onSuccess: (chat) => {
      qc.setQueryData<Chat>(chatQueryKey(chat.id), chat);
      void qc.invalidateQueries({ queryKey: CHATS_QUERY_KEY });
    },
  });
}

/** Clear a chat's source scope. */
export function useClearScope() {
  const qc = useQueryClient();
  return useMutation<Chat, Error, string>({
    mutationFn: (chatId) => chatApi.clearScope(chatId),
    onSuccess: (chat) => {
      qc.setQueryData<Chat>(chatQueryKey(chat.id), chat);
      void qc.invalidateQueries({ queryKey: CHATS_QUERY_KEY });
    },
  });
}

// ---------------------------------------------------------------------------
// Cache writers (the streaming bridge)
// ---------------------------------------------------------------------------

/**
 * Immutable cache-writer helpers built on `setQueryData`. `useChatStream`
 * receives these instead of raw `setMessages`/`setChats` setters: streamed
 * token/thinking/tool events update the `['chat', id]` messages array, and
 * lifecycle/title events update the `['chats']` list. Every writer clones the
 * cached value rather than mutating it.
 */
export interface ChatCacheWriter {
  /**
   * Update the messages array of `['chat', id]`. The updater receives the
   * current messages (or `[]` if the entry is missing) and must return a NEW
   * array. If the entry is missing entirely, a minimal placeholder chat is
   * seeded so streaming can still render.
   */
  updateMessages: (
    chatId: string,
    updater: (messages: Chat['messages']) => Chat['messages'],
  ) => void;
  /** Patch the `['chat', id]` chat object (e.g. title). No-op if absent. */
  patchChat: (chatId: string, patch: Partial<Chat>) => void;
  /** Patch a chat's metadata row in the `['chats']` list. */
  patchListEntry: (chatId: string, patch: Partial<ChatMetadata>) => void;
  /** Invalidate the `['chats']` list so it refetches from the server. */
  invalidateList: () => void;
}

/** Build a {@link ChatCacheWriter} bound to a specific QueryClient. */
export function makeChatCacheWriter(qc: QueryClient): ChatCacheWriter {
  return {
    updateMessages: (chatId, updater) => {
      qc.setQueryData<Chat>(chatQueryKey(chatId), (old) => {
        if (!old) {
          // No cached chat yet (e.g. a brand-new chat whose history query
          // hasn't resolved). Seed a minimal shell so streamed content has a
          // place to land; the subsequent invalidate/refetch reconciles the
          // rest of the chat metadata.
          return {
            id: chatId,
            title: 'New Chat',
            status: 'processing',
            messages: updater([]),
            message_count: 0,
            source_ids: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          };
        }
        return { ...old, messages: updater(old.messages ?? []) };
      });
    },
    patchChat: (chatId, patch) => {
      qc.setQueryData<Chat>(chatQueryKey(chatId), (old) => (old ? { ...old, ...patch } : old));
    },
    patchListEntry: (chatId, patch) => {
      qc.setQueryData<ChatMetadata[]>(CHATS_QUERY_KEY, (old) =>
        old?.map((c) => (c.id === chatId ? { ...c, ...patch } : c)) ?? old,
      );
    },
    invalidateList: () => {
      void qc.invalidateQueries({ queryKey: CHATS_QUERY_KEY });
    },
  };
}
