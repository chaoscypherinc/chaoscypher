// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Chat message bubble orchestrator.
 *
 * Renders a single chat message with role-based styling, context-window
 * dimming, rainbow glow during thinking, and delegates to focused
 * sub-components for thinking display, tool calls, quick actions,
 * and advanced LLM debug information.
 */

import { useState, useEffect, useRef } from 'react';
import {
  Box,
  Typography,
  Chip,
  Collapse,
  Tooltip,
  Avatar,
} from '@mui/material';
import SparkIcon from '@mui/icons-material/AutoAwesome';
import UserIcon from '@mui/icons-material/Person';
import ExpandIcon from '@mui/icons-material/ExpandMore';
import { ChatTheme } from '../../theme/chatTheme';
import type { ExtendedChatMessage } from './types';
import {
  ThinkingSection,
  ToolCallsSection,
  QuickActions,
  AdvancedDebugPanel,
  MessageContent,
} from './components';
import {
  matchesAny,
  CONFIRMATION_PATTERNS,
  APPROVAL_PATTERNS,
  PROPOSAL_PATTERNS,
} from './components/message-utils';
import type { ToolCall, ToolResultLike } from './components/message-utils';

interface MessageBubbleProps {
  /** The chat message to render */
  message: ExtendedChatMessage;
  /** Original index of this message in the full (unfiltered) messages array */
  messageIndex: number;
  /** Index of the first message still within the LLM context window */
  firstInContextIndex: number;
  /** Callback for quick action buttons (approve/decline) */
  onQuickAction: (response: string) => void;
  /** Whether this is the most recent displayed message */
  isLatest: boolean;
  /** Whether a message is currently being sent/streamed */
  loading: boolean;
  /** Tool result messages for matching against tool_calls */
  toolResults: ExtendedChatMessage[];
}

/**
 * Renders a single chat message bubble with support for:
 * - User vs assistant styling with role indicators
 * - Out-of-context dimming for messages beyond the context window
 * - Rainbow glow animation during initial "Thinking..." phase
 * - Expandable thinking/reasoning section
 * - Quick action approve/decline buttons for confirmation prompts
 * - Expandable tool call details with matched results
 * - Advanced LLM debug information panel
 * - Markdown rendering with interactive entity references
 */
export default function MessageBubble({
  message,
  messageIndex,
  firstInContextIndex,
  onQuickAction,
  isLatest,
  loading,
  toolResults,
}: MessageBubbleProps) {
  const [thinkingExpanded, setThinkingExpanded] = useState(false);
  const [advancedExpanded, setAdvancedExpanded] = useState(false);
  const [thinkingElapsed, setThinkingElapsed] = useState<number | null>(null);
  const thinkingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isUser = message.role === 'user';
  const isOutOfContext = messageIndex < firstInContextIndex;

  // Live thinking timer - only runs while actively streaming the latest message.
  // Stops when backend reports thinking duration OR when streaming ends.
  const hasThinking = !!message.thinking;
  const streamingThinkingMs = (
    message.extra_metadata as { streaming_timing?: { thinking_ms?: number } } | undefined
  )?.streaming_timing?.thinking_ms;
  const resolvedThinkingMs = message.llm_debug?.timing?.thinking_ms ?? streamingThinkingMs;
  const hasThinkingTiming = resolvedThinkingMs != null;
  const isActivelyStreaming = isLatest && loading;
  useEffect(() => {
    // Start timer only when actively streaming with thinking but no timing yet
    if (hasThinking && !hasThinkingTiming && isActivelyStreaming && !thinkingTimerRef.current) {
      const start = Date.now();
      thinkingTimerRef.current = setInterval(() => {
        setThinkingElapsed(Date.now() - start);
      }, 100);
    }
    // Stop timer when timing data arrives OR streaming ends. Label derives
    // its fallback from isActivelyStreaming, so no setState needed here.
    if (thinkingTimerRef.current && (hasThinkingTiming || !isActivelyStreaming)) {
      clearInterval(thinkingTimerRef.current);
      thinkingTimerRef.current = null;
    }
    return () => {
      if (thinkingTimerRef.current) {
        clearInterval(thinkingTimerRef.current);
        thinkingTimerRef.current = null;
      }
    };
  }, [hasThinking, hasThinkingTiming, isActivelyStreaming]);

  // ---------------------------------------------------------------------------
  // Confirmation Detection
  // ---------------------------------------------------------------------------

  const lowerContent = message.content.toLowerCase();

  const hasActionConfirmation = matchesAny(lowerContent, CONFIRMATION_PATTERNS);
  const hasApprovalQuestion = matchesAny(lowerContent, APPROVAL_PATTERNS);
  const hasActionProposal = matchesAny(lowerContent, PROPOSAL_PATTERNS);

  const hasQuestionMark = message.content.includes('?');

  const needsConfirmation = !isUser && isLatest &&
    (hasApprovalQuestion || hasActionProposal) &&
    !hasActionConfirmation &&
    (hasQuestionMark || hasActionProposal);

  // Only show rainbow when loading AND no real content yet (just "Thinking...")
  const shouldShowRainbow = !isUser && isLatest && loading && (!message.content || message.content.trim().length === 0);

  // Derived data for sub-components
  const executedToolCalls = (message.tool_calls ?? []) as ToolCall[];
  const cachedToolCalls = (message.cached_tool_calls ?? []) as ToolCall[];
  const hasToolCalls = executedToolCalls.length > 0 || cachedToolCalls.length > 0;
  const hasDetailsSection = !isUser && (message.thinking || hasToolCalls);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <Box
      sx={{
        mb: 0.5,
        opacity: isOutOfContext ? 0.5 : 1,
        transition: 'opacity 0.2s ease',
        py: 2,
      }}
    >
      <Box
        className={shouldShowRainbow ? 'rainbow-glow-wrapper' : ''}
        sx={{
          position: 'relative',
          display: 'flex',
          gap: 1.5,
          // AI messages: ghost card with left accent
          ...(!isUser ? {
            bgcolor: shouldShowRainbow ? 'rgba(10, 10, 15, 0.92)' : 'rgba(255, 255, 255, 0.02)',
            borderLeft: ChatTheme.message.assistantBorder,
            borderRadius: 1.5,
            p: 2,
          } : {}),
        }}
      >
        {isOutOfContext && (
          <Tooltip title="This message is outside the context window and won't be sent to the AI">
            <Chip
              label="Out of context"
              size="small"
              sx={{
                position: 'absolute',
                top: -10,
                right: 8,
                zIndex: 10,
                fontSize: '0.65rem',
                height: 18,
                bgcolor: 'transparent',
                border: '1px solid rgba(255, 255, 255, 0.15)',
                color: 'text.disabled',
              }}
            />
          </Tooltip>
        )}
        {/* Avatar */}
        <Avatar
          sx={{
            width: 28,
            height: 28,
            bgcolor: 'transparent',
            border: isUser ? ChatTheme.avatar.user.border : ChatTheme.avatar.assistant.border,
            flexShrink: 0,
            mt: 0.25,
          }}
        >
          {isUser
            ? <UserIcon sx={{ fontSize: 16, color: ChatTheme.avatar.user.color }} />
            : <SparkIcon sx={{ fontSize: 16, color: ChatTheme.avatar.assistant.color }} />
          }
        </Avatar>

        {/* Message body */}
        <Box sx={{ minWidth: 0, flex: 1 }}>
          {/* Message Header */}
          <Typography
            variant="caption"
            sx={{
              fontWeight: "bold",
              display: 'block',
              mb: 0.75,
              color: isUser ? ChatTheme.avatar.user.color : ChatTheme.avatar.assistant.color,
              opacity: 0.8
            }}>
            {isUser ? 'You' : 'AI Assistant'}
          </Typography>

          {/* Message Content */}
          <MessageContent
            isUser={isUser}
            loading={loading}
            isLatest={isLatest}
            content={message.content}
            referencedEntities={message.referenced_entities}
            chunkCitations={message.chunk_citations}
          />

          {/* Chip Bar - horizontal row of toggle chips */}
          {(hasDetailsSection || message.llm_debug) && (
            <Box sx={{ mt: 2, display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
              {/* Combined Thinking + Tools chip */}
              {hasDetailsSection && (() => {
                // Build thinking part of label
                const thinkingPart = resolvedThinkingMs != null
                  ? `Thought for ${(resolvedThinkingMs / 1000).toFixed(1)}s`
                  : isActivelyStreaming && thinkingElapsed != null
                    ? `Thinking... ${(thinkingElapsed / 1000).toFixed(1)}s`
                    : message.thinking ? 'View Thinking' : '';

                // Build tools part of label
                const toolCount = executedToolCalls.length + cachedToolCalls.length;
                const toolsPart = toolCount > 0 ? `${toolCount} Tool${toolCount !== 1 ? 's' : ''}` : '';

                const label = [thinkingPart, toolsPart ? `(${toolsPart})` : ''].filter(Boolean).join(' ') || 'Details';

                return (
                  <Chip
                    label={label}
                    size="small"
                    variant={thinkingExpanded ? "filled" : "outlined"}
                    onClick={() => setThinkingExpanded(!thinkingExpanded)}
                    icon={<ExpandIcon sx={{ transform: thinkingExpanded ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />}
                    sx={{ cursor: 'pointer' }}
                  />
                );
              })()}

              {/* Spacer to push Advanced to far right */}
              <Box sx={{ flexGrow: 1 }} />

              {/* Advanced - ghost link style */}
              {message.llm_debug && (
                <Typography
                  component="span"
                  variant="caption"
                  onClick={() => setAdvancedExpanded(!advancedExpanded)}
                  sx={{
                    cursor: 'pointer',
                    color: 'secondary.main',
                    fontWeight: 500,
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 0.25,
                    px: 1,
                    py: 0.25,
                    borderRadius: 1,
                    transition: 'background 0.15s',
                    '&:hover': { bgcolor: 'rgba(255, 255, 255, 0.05)' },
                  }}
                >
                  Advanced
                  <ExpandIcon sx={{ fontSize: 16, transform: advancedExpanded ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />
                </Typography>
              )}
            </Box>
          )}

          {/* Combined Thinking + Tool Calls Section */}
          <Collapse in={thinkingExpanded}>
            {!isUser && message.thinking && (
              <ThinkingSection thinking={message.thinking} />
            )}
            {hasToolCalls && (
              <ToolCallsSection
                toolCalls={executedToolCalls}
                cachedToolCalls={cachedToolCalls}
                toolResults={toolResults as ToolResultLike[]}
                toolTimings={message.llm_debug?.timing?.tool_calls}
              />
            )}
          </Collapse>

          {/* Quick Action Buttons */}
          {needsConfirmation && (
            <QuickActions onQuickAction={onQuickAction} />
          )}

          {/* Advanced LLM Debug Content */}
          {message.llm_debug && (
            <Collapse in={advancedExpanded}>
              <AdvancedDebugPanel llmDebug={message.llm_debug} />
            </Collapse>
          )}
        </Box>
      </Box>
    </Box>
  );
}
