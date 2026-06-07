// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Chat list management hook.
 *
 * Post-rearchitecture (2026-05-25): the sidebar list is backed by the
 * `['chats']` query cache (via `useChats`) and CRUD goes through optimistic
 * TanStack mutations (`useRenameChat` / `useDeleteChat` / `useClearAllChats`).
 * This hook is now a thin orchestration layer: it derives the list from the
 * cache and owns the navigation / export-blob helpers, which are pure UI.
 */

import { useCallback, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router';
import { chatApi } from '../../../services/api/chat';
import {
  useChats,
  useRenameChat,
  useDeleteChat,
  useClearAllChats,
} from '../../../services/api/useChats';
import type { ChatMetadata } from '../../../types';
import { logger } from '../../../utils/logger';

/**
 * Return type for the useChatList hook.
 */
interface UseChatListReturn {
  /** List of all chat metadata for sidebar display */
  chats: ChatMetadata[];
  /** Navigate to the new-chat screen */
  handleNewChat: (callbacks: {
    clearError: () => void;
    clearPendingScope: () => void;
    focusInput: () => void;
  }) => void;
  /** Navigate to a specific chat */
  handleSelectChat: (chatId: string) => void;
  /** Rename a chat (optimistic) */
  handleRenameChat: (chatId: string, newTitle: string) => Promise<void>;
  /** Delete a chat (optimistic); navigates away if it was the current chat */
  handleDeleteChat: (chatId: string, currentChatId: string | undefined) => Promise<void>;
  /** Export a chat as JSON */
  handleExportChat: (chatId: string) => Promise<void>;
  /** Delete all chats (optimistic) */
  handleClearAllChats: (callbacks: { clearPendingScope: () => void }) => Promise<void>;
}

/**
 * Manages the sidebar chat list and CRUD operations.
 *
 * The list is read from the `['chats']` query cache. Mutations are optimistic
 * (cache write + rollback on failure + invalidate on settle); navigation is
 * handled internally through react-router.
 */
export function useChatList(): UseChatListReturn {
  const navigate = useNavigate();
  const { data: chats = [] } = useChats();
  const renameChat = useRenameChat();
  const deleteChat = useDeleteChat();
  const clearAllChats = useClearAllChats();
  const focusInputTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clear the focus-input timer if the hook unmounts before it fires.
  useEffect(() => {
    return () => {
      if (focusInputTimer.current) clearTimeout(focusInputTimer.current);
    };
  }, []);

  const handleNewChat = useCallback((callbacks: {
    clearError: () => void;
    clearPendingScope: () => void;
    focusInput: () => void;
  }) => {
    navigate('/chat');
    callbacks.clearError();
    callbacks.clearPendingScope();
    if (focusInputTimer.current) clearTimeout(focusInputTimer.current);
    focusInputTimer.current = setTimeout(() => callbacks.focusInput(), 100);
  }, [navigate]);

  const handleSelectChat = useCallback((chatId: string) => {
    navigate(`/chat/${chatId}`);
  }, [navigate]);

  const handleRenameChat = useCallback(async (chatId: string, newTitle: string) => {
    try {
      await renameChat.mutateAsync({ chatId, title: newTitle });
    } catch (err) {
      logger.error('Failed to rename chat:', err);
    }
  }, [renameChat]);

  const handleDeleteChat = useCallback(async (
    chatId: string,
    currentChatId: string | undefined,
  ) => {
    try {
      await deleteChat.mutateAsync(chatId);
      if (currentChatId === chatId) {
        navigate('/chat');
      }
    } catch (err) {
      logger.error('Failed to delete chat:', err);
    }
  }, [deleteChat, navigate]);

  const handleExportChat = useCallback(async (chatId: string) => {
    try {
      const data = await chatApi.exportChat(chatId);
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: 'application/json',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `chat-${chatId}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      logger.error('Failed to export chat:', err);
    }
  }, []);

  const handleClearAllChats = useCallback(async (callbacks: {
    clearPendingScope: () => void;
  }) => {
    try {
      await clearAllChats.mutateAsync();
      callbacks.clearPendingScope();
      navigate('/chat');
    } catch (err) {
      logger.error('Failed to clear all chats:', err);
    }
  }, [clearAllChats, navigate]);

  return {
    chats,
    handleNewChat,
    handleSelectChat,
    handleRenameChat,
    handleDeleteChat,
    handleExportChat,
    handleClearAllChats,
  };
}
