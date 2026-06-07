// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Box,
  Typography,
} from '@mui/material';
import BotIcon from '@mui/icons-material/SmartToy';
import { CHAT_STARTERS } from '../../constants/chatStarters';
import type { ChunkCitationMap, ContextInfo, EntityReferenceMap } from '../../types';
import type { ExtendedChatMessage } from './types';
import MessageBubble from './MessageBubble';

interface IndexedMessage {
  msg: ExtendedChatMessage;
  originalIndex: number;
}

interface ChatMessageListProps {
  /** Normalized chat messages to display */
  messages: ExtendedChatMessage[];
  /** Whether a message is being sent/streamed */
  loading: boolean;
  /** Current chat status (e.g. 'processing') */
  chatStatus?: string;
  /** Context window usage information */
  contextInfo: ContextInfo | null;
  /** Callback for quick action buttons (approve/decline) */
  onQuickAction: (response: string) => void;
  /** Ref for scroll-to-bottom anchor element */
  messagesEndRef: React.RefObject<HTMLDivElement | null>;
}

/**
 * Merge consecutive assistant messages into a single message.
 *
 * When the LLM uses tools, the backend may save multiple assistant rows per turn
 * (intermediate thinking/tool_calls + final response). On reload these appear as
 * separate bubbles. This function collapses them into one merged bubble.
 */
function groupConsecutiveAssistantMessages(
  indexed: IndexedMessage[],
): IndexedMessage[] {
  const result: IndexedMessage[] = [];
  let group: IndexedMessage[] = [];

  const flushGroup = () => {
    if (group.length === 0) return;
    if (group.length === 1) {
      result.push(group[0]);
    } else {
      result.push(mergeAssistantGroup(group));
    }
    group = [];
  };

  for (const item of indexed) {
    if (item.msg.role === 'assistant') {
      group.push(item);
    } else {
      flushGroup();
      result.push(item);
    }
  }
  flushGroup();

  return result;
}

/**
 * Merge an array of consecutive assistant IndexedMessages into one.
 */
function mergeAssistantGroup(group: IndexedMessage[]): IndexedMessage {
  const last = group[group.length - 1];

  // Content: join non-empty contents
  const contents = group
    .map(g => g.msg.content)
    .filter(c => c && c.trim().length > 0);
  const mergedContent = contents.join('\n\n');

  // Thinking: join non-empty thinking with separator
  const thinkings = group
    .map(g => g.msg.thinking)
    .filter((t): t is string => !!t && t.trim().length > 0);
  const mergedThinking = thinkings.length > 0
    ? thinkings.join('\n\n---\n\n')
    : undefined;

  // Tool calls: flatMap all arrays
  const mergedToolCalls = group.flatMap(g => g.msg.tool_calls ?? []);

  // Referenced entities: merge objects (later wins)
  const mergedEntities = group.reduce<EntityReferenceMap>((acc, g) => {
    if (g.msg.referenced_entities) {
      return { ...acc, ...g.msg.referenced_entities };
    }
    return acc;
  }, {});

  // Chunk citations: merge objects (later wins)
  const mergedChunkCitations = group.reduce<ChunkCitationMap>((acc, g) => {
    if (g.msg.chunk_citations) {
      return { ...acc, ...g.msg.chunk_citations };
    }
    return acc;
  }, {});

  return {
    msg: {
      ...last.msg,
      content: mergedContent,
      thinking: mergedThinking,
      tool_calls: mergedToolCalls.length > 0 ? mergedToolCalls : undefined,
      referenced_entities: Object.keys(mergedEntities).length > 0
        ? mergedEntities
        : undefined,
      chunk_citations: Object.keys(mergedChunkCitations).length > 0
        ? mergedChunkCitations
        : undefined,
      // llm_debug: keep from last (most complete)
    },
    originalIndex: last.originalIndex,
  };
}

/**
 * Renders the scrollable message list area, including:
 * - Empty state with welcome prompt when no messages exist
 * - Filtered message display (tool messages hidden, shown inline in tool calls)
 * - Consecutive assistant message grouping into single bubbles
 * - Loading/thinking indicator during processing
 * - Scroll anchor for auto-scroll behavior
 */
export default function ChatMessageList({
  messages,
  loading,
  chatStatus: _chatStatus,
  contextInfo,
  onQuickAction,
  messagesEndRef,
}: ChatMessageListProps) {
  const toolResultMessages = messages.filter(msg => msg.role === 'tool');
  const displayMessages = messages
    .map((msg, originalIndex) => ({ msg, originalIndex }))
    .filter(({ msg }) => msg.role !== 'tool');

  const groupedMessages = groupConsecutiveAssistantMessages(displayMessages);

  return (
    <Box sx={{ flexGrow: 1, flexShrink: 1, overflowY: 'auto', p: 2, minHeight: 0 }}>
      {messages.length === 0 ? (
        <Box sx={{ textAlign: 'center', mt: 8 }}>
          <BotIcon sx={{ fontSize: 60, color: 'text.secondary', mb: 2 }} />
          <Typography variant="h5" gutterBottom sx={{
            color: "text.secondary"
          }}>
            AI Research Assistant
          </Typography>
          <Typography
            variant="body2"
            sx={{
              color: "text.secondary",
              mb: 3
            }}>
            Ask anything about your knowledge graph.
          </Typography>
          <Box sx={{ display: 'flex', gap: 1.5, justifyContent: 'center', flexWrap: 'wrap' }}>
            {CHAT_STARTERS.map((starter) => (
              <Box
                key={starter.prompt}
                onClick={() => onQuickAction(starter.prompt)}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  px: 2,
                  py: 1,
                  borderRadius: '20px',
                  border: '1px solid rgba(255, 255, 255, 0.08)',
                  bgcolor: 'rgba(255, 255, 255, 0.03)',
                  cursor: 'pointer',
                  transition: 'all 0.2s',
                  '&:hover': {
                    borderColor: 'rgba(0, 229, 255, 0.25)',
                    bgcolor: 'rgba(0, 229, 255, 0.06)',
                  },
                }}
              >
                <Typography sx={{ fontSize: 14 }}>{starter.icon}</Typography>
                <Typography sx={{ fontSize: 13, color: 'text.secondary' }}>
                  {starter.label}
                </Typography>
              </Box>
            ))}
          </Box>
        </Box>
      ) : (
        groupedMessages.map(({ msg, originalIndex }, displayIndex) => (
          <MessageBubble
            key={originalIndex}
            message={msg}
            messageIndex={originalIndex}
            firstInContextIndex={contextInfo?.first_in_context_index ?? 0}
            onQuickAction={onQuickAction}
            isLatest={displayIndex === groupedMessages.length - 1}
            loading={loading}
            toolResults={toolResultMessages}
          />
        ))
      )}
      {/* Thinking indicator handled by the message bubble's internal timer */}
      <div ref={messagesEndRef as React.RefObject<HTMLDivElement>} />
    </Box>
  );
}
