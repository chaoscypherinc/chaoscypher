// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Centralized chat theme constants.
 *
 * All chat-specific colors and styling values are defined here to avoid
 * hardcoded values scattered across chat components.
 *
 * Uses a "transcript" layout: no bubble backgrounds, left-aligned flow,
 * with AI responses anchored by a glowing left-border accent.
 */

import { CardColors, hexToRgba } from './cardStyles';
import { ChaosCypherPalette } from './palette';

/** Base color for citation blockquotes. */
const CITATION_BASE = ChaosCypherPalette.primary;

/** Tint used for thinking section backgrounds. */
const THINKING_BASE = ChaosCypherPalette.primary;

/** Tint for successful tool output blocks. */
const TOOL_SUCCESS_BASE = ChaosCypherPalette.success;

export const ChatTheme = {
  /** Message styling (transcript layout — no bubble backgrounds). */
  message: {
    /** AI response left-border accent. */
    assistantBorder: `2px solid ${hexToRgba(ChaosCypherPalette.primary, 0.4)}`,
    /** Faint separator between messages. */
    separator: '1px solid rgba(255, 255, 255, 0.05)',
  },

  /** Avatar styling. */
  avatar: {
    user: {
      border: `1px solid ${hexToRgba(ChaosCypherPalette.primary, 0.4)}`,
      color: ChaosCypherPalette.primary,
    },
    assistant: {
      border: `1px solid ${hexToRgba(ChaosCypherPalette.secondary, 0.4)}`,
      color: ChaosCypherPalette.secondary,
    },
  },

  /** Inline content styling within messages (code blocks, blockquotes). */
  content: {
    /** Code/pre block backgrounds. */
    codeBg: 'rgba(0, 0, 0, 0.3)',
    /** Blockquote left border color. */
    blockquoteBorder: 'rgba(255, 255, 255, 0.15)',
  },

  /** Citation blockquote styling. */
  citation: {
    borderColor: CITATION_BASE,
    bg: hexToRgba(CITATION_BASE, 0.06),
    /** Icon colors for validation verdicts. */
    verdictCorrect: CardColors.success,
    verdictWrong: CardColors.error,
  },

  /** Thinking/reasoning section styling. */
  thinking: {
    bg: hexToRgba(THINKING_BASE, 0.06),
    border: `1px solid ${hexToRgba(THINKING_BASE, 0.15)}`,
  },

  /** Tool call display styling. */
  tools: {
    /** Background for tool output containers. */
    outputBg: 'rgba(0, 0, 0, 0.2)',
    /** Background for successful tool output pre blocks. */
    outputSuccessBg: hexToRgba(TOOL_SUCCESS_BASE, 0.05),
    /** Border for cached (deduplicated) tool call separators. */
    cachedBorder: 'rgba(128, 128, 128, 0.15)',
  },

  /** Input terminal styling. */
  input: {
    bg: 'rgba(5, 5, 10, 0.6)',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    blur: 'blur(16px)',
  },
} as const;
