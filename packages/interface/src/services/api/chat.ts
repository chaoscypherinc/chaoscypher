// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient, API_BASE } from './client';
import type { Chat, ChatMessage, ChatMetadata } from '../../types';
import type { PaginationMetadata } from '../crudApiFactory';

interface PaginatedChatList {
  data: ChatMetadata[];
  pagination: PaginationMetadata;
}

export const chatApi = {
  sendMessage: async (message: string, chatId?: string): Promise<ChatMessage> => {
    if (!chatId) {
      throw new Error('Chat ID is required');
    }
    const response = await apiClient.post<ChatMessage>(`/chats/${chatId}/messages`, {
      role: 'user',
      content: message,
    });
    return response.data;
  },
  createChat: async (options?: {
    title?: string;
    source_ids?: string[];
    tag_ids?: string[];
  }): Promise<Chat> => {
    const response = await apiClient.post<Chat>('/chats', {
      title: options?.title || 'New Chat',
      source_ids: options?.source_ids,
      tag_ids: options?.tag_ids,
    });
    return response.data;
  },
  listChats: async (): Promise<ChatMetadata[]> => {
    const response = await apiClient.get<PaginatedChatList>('/chats');
    return response.data.data;
  },
  getChat: async (chatId: string): Promise<Chat> => {
    const response = await apiClient.get<Chat>(`/chats/${chatId}`);
    return response.data;
  },
  updateTitle: async (chatId: string, title: string): Promise<Chat> => {
    const response = await apiClient.patch<Chat>(`/chats/${chatId}`, { title });
    return response.data;
  },
  deleteChat: async (chatId: string): Promise<{ success: true }> => {
    await apiClient.delete(`/chats/${chatId}`);
    return { success: true };
  },
  deleteAllChats: async (): Promise<void> => {
    await apiClient.delete('/chats');
  },
  exportChat: async (chatId: string): Promise<Chat> => {
    const response = await apiClient.get<{ data: Chat }>(`/chats/${chatId}/export`);
    return response.data.data;
  },
  generateTitle: async (chatId: string): Promise<{ title: string }> => {
    const response = await apiClient.post<{ title: string }>(`/chats/${chatId}/generate_title`);
    return response.data;
  },
  updateScope: async (
    chatId: string,
    sourceIds?: string[],
    tagIds?: string[]
  ): Promise<Chat> => {
    const response = await apiClient.patch<Chat>(`/chats/${chatId}/scope`, {
      source_ids: sourceIds,
      tag_ids: tagIds,
    });
    return response.data;
  },
  clearScope: async (chatId: string): Promise<Chat> => {
    const response = await apiClient.delete<Chat>(`/chats/${chatId}/scope`);
    return response.data;
  },
  /** Submit a message for background processing. Returns task_id. */
  send: async (chatId: string, content: string): Promise<{ task_id: string; status: string }> => {
    const response = await apiClient.post<{ task_id: string; status: string }>(
      `/chats/${chatId}/send`,
      { content },
    );
    return response.data;
  },
  /** Get the SSE events URL for live streaming from the background worker. */
  getEventsUrl: (chatId: string): string => {
    return `${API_BASE}/chats/${chatId}/events`;
  },
  /**
   * Approve or reject a pending tool call. The backend stream is blocked
   * until this decision is recorded.
   *
   * - Returns 204 on success.
   * - Returns 404 if the tool call is no longer pending (already decided,
   *   timed out, or the chat stream ended). Callers can treat 404 as a
   *   no-op since the UI state is already stale.
   */
  decideTool: async (
    chatId: string,
    toolCallId: string,
    decision: 'approve' | 'reject',
  ): Promise<void> => {
    await apiClient.post(`/chats/${chatId}/tool_decision`, {
      tool_call_id: toolCallId,
      decision,
    });
  },
};
