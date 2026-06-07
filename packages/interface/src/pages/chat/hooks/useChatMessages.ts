// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Chat message state and send orchestration hook.
 *
 * Post-rearchitecture (2026-05-25): the current chat + its history live in the
 * `['chat', id]` query cache (read via `useChat`, written via the
 * `cacheWriter`); this hook no longer owns `currentChat`/`messages` `useState`
 * and no longer exposes `setMessages`/`setCurrentChat`. It still owns the
 * genuine stream-control / editor state — input, loading, error,
 * isStreamingActive, contextInfo, pendingScope, pendingApproval — and the
 * send/scope orchestration that drives `useChatStream`.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { chatApi } from '../../../services/api/chat';
import {
  useChat as useChatQuery,
  useCreateChat,
  useUpdateScope,
  useClearScope,
  makeChatCacheWriter,
} from '../../../services/api/useChats';
import type {
  Chat,
  ChatError,
  ContextInfo,
  PendingToolApproval,
} from '../../../types';
import { logger } from '../../../utils/logger';
import type { ExtendedChatMessage } from '../types';
import { getApiErrorMessage, isAbortError } from '../../../utils/errors';
import { normalizeMessages } from './normalizeMessages';
import { useChatStream } from './useChatStream';

/**
 * Return type for the useChatMessages hook.
 */
interface UseChatMessagesReturn {
  /** Currently loaded chat (full data), from the `['chat', id]` cache */
  currentChat: Chat | null;
  /** Normalized messages for the current chat */
  messages: ExtendedChatMessage[];
  /** Current text input value */
  input: string;
  /** Update the text input value */
  setInput: (value: string) => void;
  /** Whether a message is being sent/streamed */
  loading: boolean;
  /** Current error state */
  error: ChatError | null;
  /** Set error (for retry action) */
  setError: (error: ChatError | null) => void;
  /** Clear the current error */
  clearError: () => void;
  /** Whether SSE streaming is actively receiving events */
  isStreamingActive: boolean;
  /** Context window usage information from the backend */
  contextInfo: ContextInfo | null;
  /** Ref for the messages scroll anchor */
  messagesEndRef: React.RefObject<HTMLDivElement | null>;
  /** Ref for the text input element */
  inputRef: React.RefObject<HTMLInputElement | null>;
  /** Send a message (or a quick action message) */
  handleSend: (quickActionMessage?: string) => Promise<void>;
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
  /**
   * Send the user's approve/reject decision to the backend. Resolves once
   * the backend acknowledges (or throws on HTTP failure). Clears the
   * pending-approval state on success.
   */
  decideToolApproval: (decision: 'approve' | 'reject') => Promise<void>;
  /** Dismiss the pending approval locally without sending a decision. */
  clearPendingApproval: () => void;
}

/**
 * Manages message state, send orchestration, and scope for the current chat.
 *
 * Reads the current chat from the `['chat', id]` query cache, drives the SSE
 * stream (which writes partials into that same cache), and owns auto-scroll,
 * input focus, URL-based chat loading, and initial-message auto-send from the
 * omnibar.
 */
export function useChatMessages(): UseChatMessagesReturn {
  const { chatId: urlChatId } = useParams<{ chatId?: string }>();
  const navigate = useNavigate();
  const routerLocation = useLocation();
  const queryClient = useQueryClient();

  // ---------------------------------------------------------------------------
  // Cache-backed current chat + messages
  // ---------------------------------------------------------------------------

  const { data: chatData } = useChatQuery(urlChatId);
  const currentChat = chatData ?? null;
  const messages = useMemo(
    () => normalizeMessages(currentChat?.messages || []),
    [currentChat?.messages],
  );

  const cacheWriter = useMemo(() => makeChatCacheWriter(queryClient), [queryClient]);
  const createChat = useCreateChat();
  const updateScope = useUpdateScope();
  const clearScope = useClearScope();

  // ---------------------------------------------------------------------------
  // Stream-control / editor state (genuine UI state — stays useState)
  // ---------------------------------------------------------------------------

  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ChatError | null>(null);
  const [isStreamingActive, setIsStreamingActive] = useState(false);
  const [contextInfo, setContextInfo] = useState<ContextInfo | null>(null);

  const [pendingScope, setPendingScope] = useState<string[]>([]);
  const [pendingApproval, setPendingApproval] = useState<PendingToolApproval | null>(null);
  const initialMessageHandledRef = useRef(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // ---------------------------------------------------------------------------
  // Stream Hook
  // ---------------------------------------------------------------------------

  const { streamEvents, lastStreamEventTimeRef } = useChatStream(
    {
      cacheWriter,
      setLoading,
      setIsStreamingActive,
      setContextInfo,
      setError,
      setPendingApproval,
    },
    currentChat,
    isStreamingActive,
  );

  // ---------------------------------------------------------------------------
  // Scroll & Focus Effects
  // ---------------------------------------------------------------------------

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // Auto-focus input on mount and when chat changes
  useEffect(() => {
    const timer = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(timer);
  }, [urlChatId]);

  // Restore focus when loading completes (after streaming finishes)
  useEffect(() => {
    if (!loading && !isStreamingActive) {
      const timer = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(timer);
    }
  }, [loading, isStreamingActive]);

  // ---------------------------------------------------------------------------
  // Send Message (SSE Streaming)
  // ---------------------------------------------------------------------------

  const handleSend = useCallback(async (quickActionMessage?: string) => {
    const messageToSend = quickActionMessage || input;

    if (!messageToSend.trim()) return;
    if (loading) return;

    // Clear input only if it's not a quick action
    if (!quickActionMessage) {
      setInput('');
    }
    setError(null);
    setLoading(true);
    setIsStreamingActive(true);
    lastStreamEventTimeRef.current = Date.now();

    /** Append the optimistic user message + assistant placeholder into the
     * `['chat', id]` cache (immutably). */
    const appendOptimistic = (chatId: string) => {
      cacheWriter.updateMessages(chatId, (prev) => [
        ...prev,
        { role: 'user', content: messageToSend },
        { role: 'assistant', content: '', thinking: '...' },
      ]);
    };

    try {
      // Create a new chat if one doesn't exist. `useCreateChat` seeds the
      // `['chat', newId]` cache + invalidates the list; we then navigate and
      // write the optimistic messages into the freshly-seeded cache entry so
      // the in-flight stream has somewhere to land.
      let chatId: string | undefined = currentChat?.id;
      let wasNewChat = false;
      if (!chatId) {
        const createOptions = pendingScope.length > 0 ? { source_ids: pendingScope } : {};
        const newChat = await createChat.mutateAsync(createOptions);
        chatId = newChat.id;
        wasNewChat = true;
        setPendingScope([]);
        navigate(`/chat/${chatId}`);
        appendOptimistic(chatId);
      } else {
        appendOptimistic(chatId);
      }

      // Submit message for background processing
      await chatApi.send(chatId, messageToSend);

      // Subscribe to live events via SSE
      await streamEvents(chatId, wasNewChat);
    } catch (err) {
      // Ignore abort errors (caused by component unmount or new stream replacing old one)
      if (isAbortError(err)) return;
      logger.error('Chat error:', err);
      setError({
        message: getApiErrorMessage(err) || 'Failed to send message',
        code: 'NETWORK_ERROR',
        details: {
          is_retryable: true,
          suggested_action: 'Check your network connection and try again.',
        },
      });
      setLoading(false);
      setIsStreamingActive(false);
    }
  }, [input, loading, currentChat, pendingScope, navigate, createChat, cacheWriter, streamEvents, lastStreamEventTimeRef]);

  // ---------------------------------------------------------------------------
  // Auto-send initial message from omnibar (passed via router state)
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const initialMessage = (routerLocation.state as { initialMessage?: string } | null)?.initialMessage;
    if (initialMessage && !initialMessageHandledRef.current) {
      initialMessageHandledRef.current = true;
      // Clear the state so a browser back/forward doesn't re-trigger
      navigate(routerLocation.pathname, { replace: true, state: {} });
      // Small delay to ensure component is fully mounted
      const timer = setTimeout(() => handleSend(initialMessage), 50);
      return () => clearTimeout(timer);
    }
  }, [routerLocation.state, routerLocation.pathname, navigate, handleSend]);

  // ---------------------------------------------------------------------------
  // Scope Management
  // ---------------------------------------------------------------------------

  const handleUpdateScope = useCallback(async (sourceIds: string[]) => {
    if (!currentChat?.id) return;
    try {
      await updateScope.mutateAsync({ chatId: currentChat.id, sourceIds });
    } catch (err) {
      logger.error('Failed to update scope:', err);
    }
  }, [currentChat, updateScope]);

  const handleClearScope = useCallback(async () => {
    if (!currentChat?.id) return;
    try {
      await clearScope.mutateAsync(currentChat.id);
    } catch (err) {
      logger.error('Failed to clear scope:', err);
    }
  }, [currentChat, clearScope]);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  // ---------------------------------------------------------------------------
  // Tool Approval
  // ---------------------------------------------------------------------------

  /**
   * POST the user's approve/reject decision to the backend. On success,
   * the server-side stream resumes and sends either `tool_calls` +
   * `tool_result` (approve path) or `tool_rejected` (reject path).
   *
   * A 404 means the tool call is no longer pending (already decided or
   * the stream ended). We swallow the 404 and clear local state — the UI
   * was stale.
   */
  const decideToolApproval = useCallback(
    async (decision: 'approve' | 'reject') => {
      const approval = pendingApproval;
      const chatId = currentChat?.id;
      if (!approval || !chatId) return;
      try {
        await chatApi.decideTool(chatId, approval.tool_call_id, decision);
        // Clear only if the pending approval hasn't changed (race guard:
        // a subsequent approval_required event could land before we finish).
        setPendingApproval((prev) =>
          prev && prev.tool_call_id === approval.tool_call_id ? null : prev,
        );
      } catch (err) {
        // 404 → tool call is no longer pending (race with timeout or
        // stream ending). Clear local state so the user isn't stuck.
        if (err && typeof err === 'object' && 'status' in err && err.status === 404) {
          logger.warn('tool_decision 404 — tool call no longer pending, clearing');
          setPendingApproval((prev) =>
            prev && prev.tool_call_id === approval.tool_call_id ? null : prev,
          );
          return;
        }
        logger.error('Failed to submit tool decision:', err);
        throw err;
      }
    },
    [pendingApproval, currentChat],
  );

  const clearPendingApproval = useCallback(() => {
    setPendingApproval(null);
  }, []);

  return {
    currentChat,
    messages,
    input,
    setInput,
    loading,
    error,
    setError,
    clearError,
    isStreamingActive,
    contextInfo,
    messagesEndRef,
    inputRef,
    handleSend,
    handleUpdateScope,
    handleClearScope,
    pendingScope,
    setPendingScope,
    pendingApproval,
    decideToolApproval,
    clearPendingApproval,
  };
}
