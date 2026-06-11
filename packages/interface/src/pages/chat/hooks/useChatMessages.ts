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
  /**
   * Request cancellation of the in-flight turn. The worker stops at the
   * next step boundary; the `done {status: "cancelled"}` event (not this
   * call) is what ends the spinner, so the partial answer stays truthful.
   */
  handleStop: () => Promise<void>;
  /**
   * Re-run the last turn after a worker failure WITHOUT re-posting the
   * user message (it is already persisted; re-sending would duplicate it).
   */
  handleRetry: () => Promise<void>;
  /** Drop the last answer and re-run the turn (Regenerate button). */
  handleRegenerate: () => Promise<void>;
  /** Arm edit-and-resend for a persisted user message (populates the input). */
  startEditMessage: (messageId: string, content: string) => void;
  /** True between a Stop click and the turn actually ending (button guard). */
  stopping: boolean;
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
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<ChatError | null>(null);
  const [isStreamingActive, setIsStreamingActive] = useState(false);
  const [contextInfo, setContextInfo] = useState<ContextInfo | null>(null);

  const [pendingScope, setPendingScope] = useState<string[]>([]);
  const [pendingApproval, setPendingApproval] = useState<PendingToolApproval | null>(null);
  /** When set, the next send REPLACES this user message (edit-and-resend). */
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
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
  // Focus Effects (scroll-follow lives in ChatMessageList, which owns the
  // scroll container and knows whether the reader is near the bottom)
  // ---------------------------------------------------------------------------

  // Auto-focus input on mount and when chat changes
  useEffect(() => {
    const timer = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(timer);
  }, [urlChatId]);

  // Stale chat-A context stats must not dim chat-B messages, and chat-A
  // errors must not show on chat B (2026-06-10 audit).
  useEffect(() => {
    setContextInfo(null);
    setError(null);
  }, [urlChatId]);

  // Restore focus when loading completes (after streaming finishes)
  useEffect(() => {
    if (!loading && !isStreamingActive) {
      const timer = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(timer);
    }
  }, [loading, isStreamingActive]);

  // The turn is over (done/error event flipped loading) — Stop is re-armed.
  useEffect(() => {
    if (!loading) setStopping(false);
  }, [loading]);

  // ---------------------------------------------------------------------------
  // Refresh-resilience + cross-chat isolation
  // ---------------------------------------------------------------------------

  /** Chat that owns the in-flight stream; null during new-chat creation. */
  const [streamingChatId, setStreamingChatId] = useState<string | null>(null);
  /** Guards the reattach effect against re-subscribing the same chat. */
  const resubscribedForRef = useRef<string | null>(null);

  // A loaded chat that is still processing in the background gets its live
  // stream (and spinner) reattached — covers page refresh mid-turn AND
  // switching back to a processing chat. Without this the page is blind
  // until the slow poll catches up (2026-06-10 audit).
  useEffect(() => {
    const id = currentChat?.id;
    if (!id || currentChat?.status !== 'processing') return;
    if (resubscribedForRef.current === id || isStreamingActive) return;
    resubscribedForRef.current = id;
    setStreamingChatId(id);
    setLoading(true);
    setIsStreamingActive(true);
    lastStreamEventTimeRef.current = Date.now();
    void streamEvents(id, false).catch((err) => {
      if (isAbortError(err)) return;
      logger.error('Failed to reattach chat stream:', err);
      setLoading(false);
      setIsStreamingActive(false);
    });
  }, [currentChat?.id, currentChat?.status, isStreamingActive, streamEvents, lastStreamEventTimeRef]);

  // Re-arm the reattach guard when leaving the chat so revisiting works.
  useEffect(() => {
    if (resubscribedForRef.current && resubscribedForRef.current !== urlChatId) {
      resubscribedForRef.current = null;
    }
  }, [urlChatId]);

  // Stream flags shown to the UI are scoped to the OWNING chat — chat A's
  // spinner must never render on chat B. (The null sentinel covers the
  // new-chat window before an id exists.)
  const ownsStream =
    streamingChatId === null || streamingChatId === (currentChat?.id ?? streamingChatId);
  const loadingForThisChat = loading && ownsStream;
  const isStreamingForThisChat = isStreamingActive && ownsStream;

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
    // Until the chat id is resolved (new-chat creation), no chat owns the
    // stream — the null sentinel keeps the spinner on the page being viewed.
    setStreamingChatId(currentChat?.id ?? null);
    lastStreamEventTimeRef.current = Date.now();

    // Edit-and-resend: the armed message (and everything after it) is
    // replaced server-side; mirror that in the optimistic cache write.
    const replaceFromId = quickActionMessage ? null : editingMessageId;
    if (replaceFromId) setEditingMessageId(null);

    /** Write the optimistic user message + assistant placeholder into the
     * `['chat', id]` cache (immutably), honoring an armed edit. */
    const appendOptimistic = (chatId: string) => {
      cacheWriter.updateMessages(chatId, (prev) => {
        let kept = prev;
        if (replaceFromId) {
          const anchorIdx = prev.findIndex((m) => (m as { id?: string }).id === replaceFromId);
          if (anchorIdx >= 0) kept = prev.slice(0, anchorIdx);
        }
        return [
          ...kept,
          { role: 'user', content: messageToSend },
          { role: 'assistant', content: '', thinking: '...' },
        ];
      });
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

      // The id is resolved (created or existing): it owns the stream now.
      setStreamingChatId(chatId);

      // Submit message for background processing
      await chatApi.send(chatId, messageToSend, replaceFromId ?? undefined);

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
  }, [input, loading, currentChat, pendingScope, editingMessageId, navigate, createChat, cacheWriter, streamEvents, lastStreamEventTimeRef]);

  // ---------------------------------------------------------------------------
  // Stop / cancel the in-flight turn
  // ---------------------------------------------------------------------------

  /**
   * POST /chats/{id}/cancel. Does NOT optimistically flip `loading` — the
   * worker's `done {status: "cancelled"}` event is the single source of
   * truth for the turn ending (it carries the persisted partial answer).
   * 404/409 mean the turn already finished; treat as a no-op.
   */
  const handleStop = useCallback(async () => {
    const chatId = currentChat?.id;
    if (!chatId || !loading || stopping) return;
    setStopping(true);
    try {
      await chatApi.cancel(chatId);
    } catch (err) {
      const status =
        err && typeof err === 'object' && 'status' in err ? (err as { status: unknown }).status : null;
      if (status === 404 || status === 409) {
        return; // turn already over — the done/error event will land shortly
      }
      logger.error('Failed to cancel chat turn:', err);
      setStopping(false); // genuine failure: let the user try again
    }
  }, [currentChat, loading, stopping]);

  // ---------------------------------------------------------------------------
  // Retry a failed turn
  // ---------------------------------------------------------------------------

  /**
   * POST /chats/{id}/retry then reattach the stream. Used by the error
   * panel's Retry button for worker/stream failures (where the user message
   * is already persisted). A failed retry surfaces as a fresh error.
   */
  const handleRetry = useCallback(async () => {
    const chatId = currentChat?.id;
    if (!chatId || loading) return;
    setError(null);
    setLoading(true);
    setIsStreamingActive(true);
    setStreamingChatId(chatId);
    lastStreamEventTimeRef.current = Date.now();
    try {
      await chatApi.retry(chatId);
      await streamEvents(chatId, false);
    } catch (err) {
      if (isAbortError(err)) return;
      logger.error('Chat retry error:', err);
      setError({
        message: getApiErrorMessage(err) || 'Failed to retry',
        code: 'NETWORK_ERROR',
        details: {
          is_retryable: true,
          suggested_action: 'Check your network connection and try again.',
        },
      });
      setLoading(false);
      setIsStreamingActive(false);
    }
  }, [currentChat, loading, streamEvents, lastStreamEventTimeRef]);

  // ---------------------------------------------------------------------------
  // Regenerate the last answer
  // ---------------------------------------------------------------------------

  /**
   * POST /chats/{id}/regenerate then reattach the stream. The backend drops
   * everything after the last user message; the cache mirrors that
   * optimistically (trailing assistant rows replaced by the placeholder).
   */
  const handleRegenerate = useCallback(async () => {
    const chatId = currentChat?.id;
    if (!chatId || loading) return;
    setError(null);
    setLoading(true);
    setIsStreamingActive(true);
    setStreamingChatId(chatId);
    lastStreamEventTimeRef.current = Date.now();
    cacheWriter.updateMessages(chatId, (prev) => {
      const roles = prev.map((m) => m.role);
      const lastUserIdx = roles.lastIndexOf('user');
      const kept = lastUserIdx >= 0 ? prev.slice(0, lastUserIdx + 1) : prev;
      return [...kept, { role: 'assistant', content: '', thinking: '...' }];
    });
    try {
      await chatApi.regenerate(chatId);
      await streamEvents(chatId, false);
    } catch (err) {
      if (isAbortError(err)) return;
      logger.error('Chat regenerate error:', err);
      setError({
        message: getApiErrorMessage(err) || 'Failed to regenerate',
        code: 'NETWORK_ERROR',
        details: {
          is_retryable: true,
          suggested_action: 'Check your network connection and try again.',
        },
      });
      setLoading(false);
      setIsStreamingActive(false);
    }
  }, [currentChat, loading, cacheWriter, streamEvents, lastStreamEventTimeRef]);

  // ---------------------------------------------------------------------------
  // Edit-and-resend
  // ---------------------------------------------------------------------------

  /** Populate the input with a persisted user message and arm replacement. */
  const startEditMessage = useCallback((messageId: string, content: string) => {
    setEditingMessageId(messageId);
    setInput(content);
    inputRef.current?.focus();
  }, []);

  // Clearing the input (or switching chats) disarms a pending edit so a
  // fresh question can never silently replace an old message.
  const setInputAndMaybeDisarm = useCallback((value: string) => {
    setInput(value);
    if (value === '') setEditingMessageId(null);
  }, []);

  useEffect(() => {
    setEditingMessageId(null);
  }, [urlChatId]);

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
    setInput: setInputAndMaybeDisarm,
    loading: loadingForThisChat,
    error,
    setError,
    clearError,
    isStreamingActive: isStreamingForThisChat,
    contextInfo,
    messagesEndRef,
    inputRef,
    handleSend,
    handleStop,
    handleRetry,
    handleRegenerate,
    startEditMessage,
    stopping,
    handleUpdateScope,
    handleClearScope,
    pendingScope,
    setPendingScope,
    pendingApproval,
    decideToolApproval,
    clearPendingApproval,
  };
}
