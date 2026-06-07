// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SSE streaming and polling fallback hook for chat messages.
 *
 * Manages the connection lifecycle for real-time event streams from the
 * backend worker. Event parsing is delegated to `handleStreamEvent`.
 * Falls back to periodic polling when SSE is unavailable.
 *
 * Post-rearchitecture (2026-05-25): the streamed conversation is written into
 * the `['chat', id]` query cache via the injected `cacheWriter` rather than
 * into passed-in `setMessages`/`setChats` setters. The cache is the single
 * source of truth; this hook only owns connection lifecycle + stream-control
 * flags.
 */

import { useCallback, useEffect, useRef } from 'react';
import { useAppConfig } from '../../../contexts/useAppConfig';
import { chatApi } from '../../../services/api/chat';
import type { Chat } from '../../../types';
import { normalizeMessages } from './normalizeMessages';
import { logger } from '../../../utils/logger';
import { createAccumulator, handleStreamEvent } from './handleStreamEvent';
import type { StreamCallbacks, UseChatStreamReturn } from './useChatStream.types';

/**
 * Manages SSE streaming and polling fallback for real-time chat messages.
 *
 * The `streamEvents` function opens an SSE connection, reads chunks from
 * the response body, and delegates each parsed event to `handleStreamEvent`.
 * Polling runs as a degraded-mode fallback on a 5-second interval.
 *
 * @param callbacks   cache writer + stream-control setters
 * @param currentChat the chat currently loaded (drives polling), or null
 * @param isStreamingActive whether SSE is actively delivering events
 */
export function useChatStream(
  callbacks: StreamCallbacks,
  currentChat: Chat | null,
  isStreamingActive: boolean,
): UseChatStreamReturn {
  const config = useAppConfig();
  const chatPollMs = config.intervals_chat_poll_ms;
  const sseRecentEventWindowMs = config.intervals_sse_recent_event_window_ms;
  const pollingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastStreamEventTimeRef = useRef<number>(0);
  const streamAbortControllerRef = useRef<AbortController | null>(null);

  // Refs for values read inside the polling interval — avoids recreating
  // the interval every time the chat object or streaming flag changes.
  const currentChatRef = useRef(currentChat);
  const isStreamingActiveRef = useRef(isStreamingActive);
  useEffect(() => {
    currentChatRef.current = currentChat;
  }, [currentChat]);
  useEffect(() => {
    isStreamingActiveRef.current = isStreamingActive;
  }, [isStreamingActive]);

  const {
    cacheWriter,
    setLoading,
    setIsStreamingActive,
    setContextInfo,
    setError,
    setPendingApproval,
  } = callbacks;

  // ---------------------------------------------------------------------------
  // Polling
  // ---------------------------------------------------------------------------

  const stopPolling = useCallback(() => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollingIntervalRef.current || !currentChatRef.current) return;

    // Don't poll if tab is hidden
    if (document.hidden) return;

    pollingIntervalRef.current = setInterval(async () => {
      try {
        // Skip polling if we're actively streaming via SSE
        if (isStreamingActiveRef.current) return;

        // Also skip if we received a stream event recently (within the
        // operator-tunable SSE recency window).
        const timeSinceLastEvent = Date.now() - lastStreamEventTimeRef.current;
        if (timeSinceLastEvent < sseRecentEventWindowMs) return;

        const chat = currentChatRef.current;
        if (!chat) return;

        const updated = await chatApi.getChat(chat.id);

        // Write the freshly-fetched chat (history + status) into the cache,
        // re-normalizing messages so the render layer sees flattened fields.
        cacheWriter.updateMessages(updated.id, () => normalizeMessages(updated.messages || []));
        cacheWriter.patchChat(updated.id, {
          status: updated.status,
          message_count: updated.message_count,
          title: updated.title,
          source_ids: updated.source_ids,
          updated_at: updated.updated_at,
        });
        cacheWriter.patchListEntry(updated.id, {
          status: updated.status,
          message_count: updated.message_count,
        });

        if (updated.status !== 'processing') {
          stopPolling();
          setLoading(false);
        }
      } catch (err) {
        logger.error('Polling error:', err);
      }
    }, chatPollMs);
  }, [stopPolling, cacheWriter, setLoading, chatPollMs, sseRecentEventWindowMs]);

  // Start polling when chat is processing; pause when tab is hidden.
  useEffect(() => {
    if (currentChat?.status === 'processing') {
      startPolling();
    } else {
      stopPolling();
    }

    const handleVisibilityChange = () => {
      if (document.hidden) {
        stopPolling();
      } else if (currentChat?.status === 'processing') {
        startPolling();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      stopPolling();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [currentChat?.status, currentChat?.id, startPolling, stopPolling]);

  // Abort in-flight stream on unmount
  useEffect(() => {
    return () => {
      streamAbortControllerRef.current?.abort();
    };
  }, []);

  // ---------------------------------------------------------------------------
  // SSE Event Stream
  // ---------------------------------------------------------------------------

  const streamEvents = useCallback(async (chatId: string, wasNewChat: boolean) => {
    // Abort any previous stream
    streamAbortControllerRef.current?.abort();
    const controller = new AbortController();
    streamAbortControllerRef.current = controller;

    const eventsUrl = chatApi.getEventsUrl(chatId);
    const response = await fetch(eventsUrl, {
      signal: controller.signal,
      credentials: 'include',
    });

    if (!response.ok) {
      logger.warn('Events SSE failed, falling back to polling');
      // Clear stream-control flags BEFORE starting polling. Otherwise `loading`
      // keeps the input permanently disabled AND the poll guard
      // (`if (isStreamingActiveRef.current) return`) neuters the fallback —
      // stranding the user on an infinite spinner with no in-app recovery.
      setLoading(false);
      setIsStreamingActive(false);
      startPolling();
      return;
    }

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const acc = createAccumulator();

    // Bind the message updater to this chat's cache entry. `handleStreamEvent`
    // calls `setMessages((prev) => next)`; we route that updater through the
    // immutable cache writer.
    const dispatchers = {
      setMessages: (updater: (prev: Chat['messages']) => Chat['messages']) =>
        cacheWriter.updateMessages(chatId, updater),
      setLoading,
      setIsStreamingActive,
      setContextInfo,
      setError,
      setPendingApproval,
    };

    const doneCallbacks = {
      onDone: async (dChatId: string, dWasNewChat: boolean) => {
        // Auto-generate title for newly created chats, then patch the list +
        // the cached chat object.
        if (dWasNewChat && dChatId) {
          chatApi.generateTitle(dChatId).then((updated) => {
            if (updated?.title && updated.title !== 'New Chat') {
              cacheWriter.patchListEntry(dChatId, { title: updated.title });
              cacheWriter.patchChat(dChatId, { title: updated.title });
            }
          }).catch(() => {
            // Silently ignore title generation failures
          });
        }

        // Refresh the sidebar list (status / message_count / new chat).
        cacheWriter.invalidateList();
      },
    };

    if (reader) {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (!line.trim() || line.startsWith(':')) continue;

            if (line.startsWith('data: ')) {
              const dataStr = line.substring(6).trim();
              if (!dataStr) continue;

              try {
                const data = JSON.parse(dataStr);
                const ts = handleStreamEvent(data, acc, dispatchers, chatId, wasNewChat, doneCallbacks);
                if (ts > 0) {
                  lastStreamEventTimeRef.current = ts;
                }
              } catch (parseErr) {
                logger.error('Failed to parse SSE event:', parseErr);
              }
            }
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          return; // Expected: stream was cancelled
        }
        throw err;
      }
    }

    // Ensure loading is cleared after stream completes
    if (!acc.isDone) {
      setLoading(false);
      setIsStreamingActive(false);
      cacheWriter.invalidateList();
    }
  }, [
    startPolling,
    cacheWriter,
    setLoading,
    setIsStreamingActive,
    setContextInfo,
    setError,
    setPendingApproval,
  ]);

  return {
    streamEvents,
    lastStreamEventTimeRef,
  };
}
