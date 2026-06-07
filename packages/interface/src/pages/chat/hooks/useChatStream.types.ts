// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Type definitions for the chat stream hook.
 *
 * Separated to avoid circular imports between `useChatStream` and
 * `useChatMessages`, which both reference these interfaces.
 */

import type { ChatError, ContextInfo, PendingToolApproval } from '../../../types';
import type { ChatCacheWriter } from '../../../services/api/useChats';

/**
 * Inputs the stream hook needs to write streamed events.
 *
 * Post-rearchitecture (2026-05-25) the streamed conversation lives in the
 * `['chat', id]` query cache, written via `cacheWriter`, NOT in passed-in
 * `setMessages`/`setChats`/`setCurrentChat` setters. The remaining setters
 * are the orchestrator's genuine stream-control `useState`.
 */
export interface StreamCallbacks {
  /** Immutable writers into the chat / list query cache. */
  cacheWriter: ChatCacheWriter;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
  setIsStreamingActive: React.Dispatch<React.SetStateAction<boolean>>;
  setContextInfo: React.Dispatch<React.SetStateAction<ContextInfo | null>>;
  setError: React.Dispatch<React.SetStateAction<ChatError | null>>;
  /**
   * Queue a pending tool-approval request. The UI opens the approval
   * dialog when a request is pending; the backend stream is blocked
   * server-side until `POST /chats/{id}/tool_decision` resolves it.
   */
  setPendingApproval: React.Dispatch<React.SetStateAction<PendingToolApproval | null>>;
}

/**
 * Return type for the useChatStream hook.
 */
export interface UseChatStreamReturn {
  /** Subscribe to SSE events for a chat and process them into the cache */
  streamEvents: (chatId: string, wasNewChat: boolean) => Promise<void>;
  /** Ref tracking the time of the last SSE event */
  lastStreamEventTimeRef: React.MutableRefObject<number>;
}
