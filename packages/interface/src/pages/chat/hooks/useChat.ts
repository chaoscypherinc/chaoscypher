// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Composition hook for the chat page.
 *
 * Wires together the focused sub-hooks (`useChatList`, `useChatMessages`)
 * into a single public API consumed by `ChatPage`. This file is intentionally
 * thin -- all business logic lives in the sub-hooks.
 *
 * Post-rearchitecture (2026-05-25): both sub-hooks now read/write the TanStack
 * Query cache (`['chats']` for the sidebar, `['chat', id]` for history), so
 * they no longer hand-share `useState` setters. The bridging callbacks that
 * used to pass `setChats`/`setMessages`/`setCurrentChat` around are gone.
 */

import { useCallback } from 'react';
import type {
  Chat,
  ChatMetadata,
  ChatError,
  ContextInfo,
  PendingToolApproval,
} from '../../../types';
import type { ExtendedChatMessage } from '../types';
import { useChatList } from './useChatList';
import { useChatMessages } from './useChatMessages';

/**
 * Return type for the useChat hook, exposing all chat state and actions.
 */
interface UseChatReturn {
  /** List of all chat metadata for sidebar display */
  chats: ChatMetadata[];
  /** Currently loaded chat (full data) */
  currentChat: Chat | null;
  /** Normalized messages for the current chat */
  messages: ExtendedChatMessage[];
  /** Current text input value */
  input: string;
  /** Whether a message is being sent/streamed */
  loading: boolean;
  /** Current error state */
  error: ChatError | null;
  /** Whether SSE streaming is actively receiving events */
  isStreamingActive: boolean;
  /** Context window usage information from the backend */
  contextInfo: ContextInfo | null;
  /** Ref for the messages scroll anchor */
  messagesEndRef: React.RefObject<HTMLDivElement | null>;
  /** Ref for the text input element */
  inputRef: React.RefObject<HTMLInputElement | null>;
  /** Update the text input value */
  setInput: (value: string) => void;
  /** Clear the current error */
  clearError: () => void;
  /** Send a message (or a quick action message) */
  handleSend: (quickActionMessage?: string) => Promise<void>;
  /** Start a new chat */
  handleNewChat: () => void;
  /** Navigate to a specific chat */
  handleSelectChat: (chatId: string) => void;
  /** Rename a chat */
  handleRenameChat: (chatId: string, newTitle: string) => Promise<void>;
  /** Delete a chat */
  handleDeleteChat: (chatId: string) => Promise<void>;
  /** Export a chat as JSON */
  handleExportChat: (chatId: string) => Promise<void>;
  /** Delete all chats */
  handleClearAllChats: () => Promise<void>;
  /** Set error (for retry action) */
  setError: (error: ChatError | null) => void;
  /** Update the source scope of the current chat */
  handleUpdateScope: (sourceIds: string[]) => Promise<void>;
  /** Clear the source scope of the current chat */
  handleClearScope: () => Promise<void>;
  /** Pending source scope for new chat (before first message) */
  pendingScope: string[];
  /** Set pending scope for the next new chat */
  setPendingScope: (sourceIds: string[]) => void;
  /** Pending tool-approval request from the backend, if any. */
  pendingApproval: PendingToolApproval | null;
  /** Approve or reject the currently pending tool call. */
  decideToolApproval: (decision: 'approve' | 'reject') => Promise<void>;
  /** Dismiss the pending approval locally (does not notify backend). */
  clearPendingApproval: () => void;
}

/**
 * Custom hook encapsulating all chat state management, message streaming,
 * polling logic, and CRUD operations.
 *
 * Composes `useChatList` (sidebar CRUD) and `useChatMessages` (message state,
 * send orchestration, SSE streaming) into a single facade that exposes the
 * same public API consumed by `ChatPage`.
 */
export function useChat(): UseChatReturn {
  // ---- Sub-hooks (each backed by the query cache) ----

  const list = useChatList();
  const msg = useChatMessages();

  // ---- Adapted callbacks ----

  const handleNewChat = useCallback(() => {
    list.handleNewChat({
      clearError: () => msg.setError(null),
      clearPendingScope: () => msg.setPendingScope([]),
      focusInput: () => msg.inputRef.current?.focus(),
    });
  }, [list, msg]);

  const handleDeleteChat = useCallback(
    (chatId: string) => list.handleDeleteChat(chatId, msg.currentChat?.id),
    [list, msg.currentChat?.id],
  );

  const handleClearAllChats = useCallback(
    () => list.handleClearAllChats({ clearPendingScope: () => msg.setPendingScope([]) }),
    [list, msg],
  );

  // ---- Public API (identical shape to the original hook) ----

  return {
    chats: list.chats,
    currentChat: msg.currentChat,
    messages: msg.messages,
    input: msg.input,
    loading: msg.loading,
    error: msg.error,
    isStreamingActive: msg.isStreamingActive,
    contextInfo: msg.contextInfo,
    messagesEndRef: msg.messagesEndRef,
    inputRef: msg.inputRef,
    setInput: msg.setInput,
    clearError: msg.clearError,
    handleSend: msg.handleSend,
    handleNewChat,
    handleSelectChat: list.handleSelectChat,
    handleRenameChat: list.handleRenameChat,
    handleDeleteChat,
    handleExportChat: list.handleExportChat,
    handleClearAllChats,
    setError: msg.setError,
    handleUpdateScope: msg.handleUpdateScope,
    handleClearScope: msg.handleClearScope,
    pendingScope: msg.pendingScope,
    setPendingScope: msg.setPendingScope,
    pendingApproval: msg.pendingApproval,
    decideToolApproval: msg.decideToolApproval,
    clearPendingApproval: msg.clearPendingApproval,
  };
}
