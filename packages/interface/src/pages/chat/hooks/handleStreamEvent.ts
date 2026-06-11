// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SSE event handler for chat streaming.
 *
 * Pure function that maps a parsed SSE event to the corresponding state
 * mutation. Keeps the streaming hook focused on connection lifecycle while
 * this module owns the per-event-type logic.
 */

import type {
  ChatMessage,
  ChatStreamWarning,
  ChunkCitationMap,
  ContextInfo,
  ChatError,
  PendingToolApproval,
} from '../../../types';
import type { ExtendedChatMessage } from '../types';
import { logger } from '../../../utils/logger';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Find the index of the last assistant message in a message array.
 */
function findLastAssistantIdx(msgs: ExtendedChatMessage[]): number {
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'assistant') return i;
  }
  return -1;
}

/**
 * Update the last assistant message in-place via an updater function,
 * or append a new fallback message if no assistant message exists.
 */
function updateAssistant(
  prev: ExtendedChatMessage[],
  updater: (msg: ExtendedChatMessage) => ExtendedChatMessage,
  fallback?: ExtendedChatMessage,
): ExtendedChatMessage[] {
  const idx = findLastAssistantIdx(prev);
  if (idx >= 0) {
    return [...prev.slice(0, idx), updater(prev[idx]), ...prev.slice(idx + 1)];
  }
  return fallback ? [...prev, fallback] : prev;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Mutable accumulator state shared across all events in a single stream.
 */
export interface StreamAccumulator {
  accumulatedThinking: string;
  allToolCalls: unknown[];
  allCachedToolCalls: unknown[];
  iterationContents: string[];
  currentPhaseContent: string;
  streamingTiming: Record<string, unknown>;
  toolTimings: Array<{ name: string; duration_ms: number; tool_call_id?: string }>;
  isDone: boolean;
}

/**
 * A functional message updater. Receives the current message array and must
 * return a NEW array (immutable). In the TanStack rearchitecture this is
 * backed by `cacheWriter.updateMessages(chatId, updater)` — the streamed
 * conversation lives in the `['chat', id]` query cache, not local state — but
 * the event-handler logic is identical: it always calls the updater form.
 */
export type MessagesUpdater = (
  updater: (prev: ExtendedChatMessage[]) => ExtendedChatMessage[],
) => void;

/**
 * State setters the event handler dispatches to. `setMessages` writes the
 * conversation into the query cache; the remaining setters are the
 * orchestrator's genuine stream-control `useState` (loading / streaming /
 * error / context / pending-approval).
 */
export interface EventDispatchers {
  setMessages: MessagesUpdater;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
  setIsStreamingActive: React.Dispatch<React.SetStateAction<boolean>>;
  setContextInfo: React.Dispatch<React.SetStateAction<ContextInfo | null>>;
  setError: React.Dispatch<React.SetStateAction<ChatError | null>>;
  setPendingApproval: React.Dispatch<React.SetStateAction<PendingToolApproval | null>>;
}

/**
 * Callbacks for post-done lifecycle actions (title generation, list refresh).
 */
export interface DoneCallbacks {
  onDone: (chatId: string, wasNewChat: boolean) => Promise<void>;
}

/** Create a fresh accumulator for a new stream session. */
export function createAccumulator(): StreamAccumulator {
  return {
    accumulatedThinking: '',
    allToolCalls: [],
    allCachedToolCalls: [],
    iterationContents: [],
    currentPhaseContent: '',
    streamingTiming: {},
    toolTimings: [],
    isDone: false,
  };
}

// ---------------------------------------------------------------------------
// Event Handler
// ---------------------------------------------------------------------------

/**
 * Process a single parsed SSE event, updating the accumulator and
 * dispatching the appropriate state mutations.
 *
 * Returns the timestamp to record as `lastStreamEventTime`, or 0 if the
 * event type does not update the timestamp.
 */
export function handleStreamEvent(
  data: Record<string, unknown>,
  acc: StreamAccumulator,
  dispatchers: EventDispatchers,
  chatId: string,
  wasNewChat: boolean,
  doneCallbacks: DoneCallbacks,
): number {
  const {
    setMessages,
    setLoading,
    setIsStreamingActive,
    setContextInfo,
    setError,
    setPendingApproval,
  } = dispatchers;
  const now = Date.now();

  switch (data.type) {
    // ----- iteration progress -----
    case 'iteration_progress':
      if (acc.currentPhaseContent && acc.currentPhaseContent.trim()) {
        acc.iterationContents.push(acc.currentPhaseContent);
      }
      acc.currentPhaseContent = '';
      return now;

    // ----- content delta -----
    case 'content': {
      acc.currentPhaseContent = (data.accumulated as string) || '';

      const previousContent = acc.iterationContents.join('\n\n---\n\n');
      const fullContent = previousContent
        ? previousContent + '\n\n---\n\n' + acc.currentPhaseContent
        : acc.currentPhaseContent;

      let content = fullContent;
      let extractedThinking = '';
      const thinkMatch = content.match(/<think>([\s\S]*?)<\/think>/);
      if (thinkMatch) {
        extractedThinking = thinkMatch[1].trim();
        content = content.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
      }

      setMessages((prev) => updateAssistant(
        prev,
        (msg) => ({
          ...msg,
          content: content,
          thinking: extractedThinking || msg.thinking,
          tool_calls: acc.allToolCalls.length > 0 ? acc.allToolCalls : msg.tool_calls,
        }),
        {
          role: 'assistant',
          content: content,
          thinking: extractedThinking || undefined,
          tool_calls: acc.allToolCalls.length > 0 ? acc.allToolCalls : undefined,
        },
      ));
      return now;
    }

    // ----- thinking delta -----
    case 'thinking_delta':
      acc.accumulatedThinking = (data.thinking as string) || '';
      setMessages((prev) => updateAssistant(
        prev,
        (msg) => ({ ...msg, thinking: acc.accumulatedThinking }),
        { role: 'assistant', content: '', thinking: acc.accumulatedThinking },
      ));
      return now;

    // ----- tool calls -----
    case 'tool_calls': {
      const newToolCalls = (data.tool_calls as unknown[]) || [];
      acc.allToolCalls = [...acc.allToolCalls, ...newToolCalls];
      setMessages((prev) => updateAssistant(
        prev,
        (msg) => ({
          ...msg,
          tool_calls: acc.allToolCalls,
          ...(acc.allCachedToolCalls.length > 0 ? { cached_tool_calls: acc.allCachedToolCalls } : {}),
        }),
      ));
      return now;
    }

    // ----- cached tool calls -----
    case 'cached_tool_calls': {
      const cachedCalls = (data.tool_calls as unknown[]) || [];
      acc.allCachedToolCalls = [...acc.allCachedToolCalls, ...cachedCalls];
      setMessages((prev) => updateAssistant(
        prev,
        (msg) => ({ ...msg, cached_tool_calls: acc.allCachedToolCalls }),
      ));
      return now;
    }

    // ----- tool start -----
    // Live "Running <tool>..." status during multi-minute tool loops; the
    // worker already publishes this event, the UI previously dropped it.
    case 'tool_start':
      setMessages((prev) => updateAssistant(
        prev,
        (msg) => ({ ...msg, running_tool: (data.tool as string) || undefined }),
      ));
      return now;

    // ----- tool result -----
    case 'tool_result': {
      if (data.duration_ms) {
        acc.toolTimings.push({
          name: data.tool as string,
          duration_ms: data.duration_ms as number,
          tool_call_id: data.tool_call_id as string | undefined,
        });
      }
      setMessages((prev) => updateAssistant(
        prev,
        (msg) => ({ ...msg, running_tool: undefined }),
      ));
      const toolResultMessage: ChatMessage = {
        role: 'tool',
        content: JSON.stringify(data.result),
        tool_call_id: data.tool_call_id as string | undefined,
        name: data.tool as string,
      };
      setMessages((prev) => [...prev, toolResultMessage]);
      return now;
    }

    // ----- timing update -----
    case 'timing_update':
      acc.streamingTiming = { ...acc.streamingTiming, ...data };
      setMessages((prev) => updateAssistant(
        prev,
        (msg) => ({
          ...msg,
          extra_metadata: {
            ...msg.extra_metadata,
            streaming_timing: acc.streamingTiming,
          },
        }),
      ));
      return now;

    // ----- tool approval required -----
    // Backend has paused tool execution and is waiting for the user to
    // approve/reject via POST /chats/{id}/tool_decision. The SSE stream
    // stays open server-side (up to 5 min) until a decision arrives.
    case 'tool_approval_required': {
      const pending: PendingToolApproval = {
        tool_call_id: (data.tool_call_id as string) || '',
        tool_name: (data.tool_name as string) || '(unknown tool)',
        arguments: (data.arguments as Record<string, unknown>) || {},
        iteration: typeof data.iteration === 'number' ? (data.iteration as number) : 0,
        received_at: new Date().toISOString(),
      };
      if (!pending.tool_call_id) {
        logger.error('tool_approval_required event missing tool_call_id', data);
        return now;
      }
      setPendingApproval(pending);
      return now;
    }

    // ----- tool rejected -----
    // Backend learned the user rejected (or the 5-min wait timed out).
    // Render a visible tool-result-style bubble so the rejection isn't
    // silently dropped. The LLM has received a synthetic "tool rejected"
    // response and will continue the turn on its own.
    case 'tool_rejected': {
      const toolCallId = data.tool_call_id as string | undefined;
      const toolName = (data.tool_name as string) || '(unknown tool)';
      const decision = (data.decision as 'reject' | 'timeout') || 'reject';

      // Clear any pending approval for this tool call (in case the event
      // arrives before the dialog was dismissed — e.g. a timeout).
      setPendingApproval((prev) =>
        prev && prev.tool_call_id === toolCallId ? null : prev,
      );

      const rejectionNote =
        decision === 'timeout'
          ? 'Tool call timed out waiting for approval.'
          : 'Tool call rejected by user.';

      const rejectedResultMessage: ChatMessage = {
        role: 'tool',
        content: JSON.stringify({ rejected: true, decision, reason: rejectionNote }),
        tool_call_id: toolCallId,
        name: toolName,
      };
      setMessages((prev) => [...prev, rejectedResultMessage]);
      return now;
    }

    // ----- warning -----
    // Backend detected a problem with this response (answer cut off by the
    // output limit, prompt overflowed the model context window, tool-call
    // limit reached, ...). Attach it to the assistant message so the user
    // sees why the answer may be degraded and how to work through it.
    case 'warning': {
      const warning: ChatStreamWarning = {
        kind: (data.kind as string) || 'generic',
        message: (data.message as string) || 'A problem was detected with this response.',
      };
      setMessages((prev) => updateAssistant(
        prev,
        (msg) => {
          const existing = msg.warnings ?? [];
          if (existing.some((w) => w.kind === warning.kind && w.message === warning.message)) {
            return msg;
          }
          return { ...msg, warnings: [...existing, warning] };
        },
        { role: 'assistant', content: '', warnings: [warning] },
      ));
      return now;
    }

    // ----- done -----
    case 'done':
      if (!acc.isDone) {
        acc.isDone = true;
        handleDoneEvent(data, acc, setMessages, setLoading, setIsStreamingActive);
        // Fire-and-forget lifecycle actions (title generation, list refresh)
        doneCallbacks.onDone(chatId, wasNewChat);
      }
      return 0;

    // ----- error -----
    case 'error':
      setError({
        message: (data.error as string) || 'Failed to get response',
        code: (data.error_code as string) || 'UNKNOWN_ERROR',
        details: (data.error_details as ChatError['details']) || {},
      });
      setLoading(false);
      setIsStreamingActive(false);
      return 0;

    // ----- context info -----
    case 'context_info':
      setContextInfo(data as unknown as ContextInfo);
      return 0;

    default: {
      logger.warn('unknown_sse_event_type', {
        type: data.type,
        keys: Object.keys(data),
      });
      return 0;
    }
  }
}

// ---------------------------------------------------------------------------
// Done Event (extracted for readability)
// ---------------------------------------------------------------------------

/** Finalize the assistant message with all accumulated data from the stream. */
function handleDoneEvent(
  data: Record<string, unknown>,
  acc: StreamAccumulator,
  setMessages: MessagesUpdater,
  setLoading: React.Dispatch<React.SetStateAction<boolean>>,
  setIsStreamingActive: React.Dispatch<React.SetStateAction<boolean>>,
): void {
  const finalPreviousContent = acc.iterationContents.join('\n\n---\n\n');
  const finalFullContent = finalPreviousContent
    ? (acc.currentPhaseContent
        ? finalPreviousContent + '\n\n---\n\n' + acc.currentPhaseContent
        : finalPreviousContent)
    : acc.currentPhaseContent;

  const finalThinking = acc.accumulatedThinking || (data.thinking as string) || undefined;

  const referencedEntities = (data.referenced_entities as ExtendedChatMessage['referenced_entities']) || undefined;
  const rawChunkCitations = (data.chunk_citations as ChunkCitationMap) || undefined;
  const llmDebug = (data.llm_debug as ExtendedChatMessage['llm_debug']) || undefined;
  const validation = (data.validation as ExtendedChatMessage['validation']) || undefined;

  // Merge per_citation verdicts into each citation chip
  const perCitation = validation?.per_citation;
  const chunkCitations: ChunkCitationMap | undefined = rawChunkCitations && perCitation
    ? Object.fromEntries(
        Object.entries(rawChunkCitations).map(([id, cite]) => [
          id,
          {
            ...cite,
            validation_verdict: perCitation[id]?.verdict === 'correct'
              ? 'correct' as const
              : perCitation[id]?.verdict === 'wrong'
                ? 'wrong' as const
                : null,
          },
        ]),
      )
    : rawChunkCitations;

  // Use normalized content from done event (has [[cite:...]] syntax)
  const normalizedContent = (data.content as string) || undefined;

  const cachedToolCalls = (data.cached_tool_calls as unknown[]) || undefined;
  const warnings = (data.warnings as ChatStreamWarning[]) || undefined;

  setMessages((prev) => updateAssistant(
    prev,
    (msg) => ({
      ...msg,
      ...(normalizedContent ? { content: normalizedContent } : {}),
      // '...' is the optimistic placeholder — never keep it past done
      thinking: finalThinking || (msg.thinking === '...' ? undefined : msg.thinking),
      running_tool: undefined,
      tool_calls: acc.allToolCalls.length > 0 ? acc.allToolCalls : msg.tool_calls,
      cached_tool_calls: cachedToolCalls || (acc.allCachedToolCalls.length > 0 ? acc.allCachedToolCalls : msg.cached_tool_calls),
      referenced_entities: referencedEntities || msg.referenced_entities,
      chunk_citations: chunkCitations || msg.chunk_citations,
      llm_debug: llmDebug || msg.llm_debug,
      ...(validation ? { validation } : {}),
      warnings: warnings || msg.warnings,
      extra_metadata: {
        ...msg.extra_metadata,
        cached_tool_calls: cachedToolCalls || (acc.allCachedToolCalls.length > 0 ? acc.allCachedToolCalls : undefined),
        chunk_citations: chunkCitations,
        llm_debug: llmDebug || msg.llm_debug,
        ...(validation ? { validation } : {}),
        ...(warnings ? { warnings } : {}),
      },
    }),
  ));

  // Contentless done = we subscribed after the worker finished (the events
  // bridge synthesizes done without content). The persisted answer arrives
  // via the next poll — do NOT overwrite the bubble with a fake apology.
  if ((!finalFullContent || finalFullContent.trim() === '') && !normalizedContent) {
    logger.warn('done_without_content_waiting_for_poll');
  }

  setLoading(false);
  setIsStreamingActive(false);
}
