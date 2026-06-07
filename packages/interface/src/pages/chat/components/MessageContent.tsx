// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Message content area with markdown rendering.
 *
 * Handles the styled container for chat message content including:
 * - Role-based text color (user vs assistant)
 * - Typography styles for markdown elements (code, blockquotes, headings)
 * - Citation blockquote styling
 * - "Thinking..." placeholder with animated dots during loading
 */

import { Box, Typography } from '@mui/material';
import type { SxProps, Theme } from '@mui/material';
import { ChatMarkdown } from '../../../components/chat';
import { ChatTheme } from '../../../theme/chatTheme';
import type { ChunkCitationMap, EntityReferenceMap } from '../../../types';
import LoadingDots from './LoadingDots';

/** Shared markdown content styles applied to the container. */
const markdownContentSx: SxProps<Theme> = {
  '& p': { margin: 0, marginBottom: 1 },
  '& p:last-child': { marginBottom: 0 },
  '& ul, & ol': { marginTop: 0, marginBottom: 1, paddingLeft: 3 },
  '& code': {
    backgroundColor: ChatTheme.content.codeBg,
    padding: '2px 6px',
    borderRadius: '4px',
    fontFamily: 'monospace',
    fontSize: '0.875em',
  },
  '& pre': {
    backgroundColor: ChatTheme.content.codeBg,
    padding: 2,
    borderRadius: 1.5,
    overflow: 'auto',
    marginTop: 1,
    marginBottom: 1,
    border: '1px solid rgba(255, 255, 255, 0.06)',
  },
  '& pre code': {
    backgroundColor: 'transparent',
    padding: 0,
  },
  '& blockquote': {
    borderLeft: '2px solid',
    borderColor: ChatTheme.content.blockquoteBorder,
    marginLeft: 0,
    paddingLeft: 2,
    fontStyle: 'italic',
  },
  '& blockquote.citation-blockquote': {
    borderLeft: '2px solid',
    borderColor: ChatTheme.citation.borderColor,
    backgroundColor: ChatTheme.citation.bg,
    borderRadius: '0 4px 4px 0',
    padding: '8px 12px',
    marginLeft: 0,
    marginRight: 0,
    fontSize: '0.9em',
    opacity: 0.9,
  },
  '& h1, & h2, & h3, & h4, & h5, & h6': {
    marginTop: 1,
    marginBottom: 1,
  },
};

interface MessageContentProps {
  /** Whether this message is from the user. */
  isUser: boolean;
  /** Whether the chat is in a loading/streaming state. */
  loading: boolean;
  /** Whether this is the most recent displayed message. */
  isLatest: boolean;
  /** The message text content. */
  content: string;
  /** Entity references embedded in the message. */
  referencedEntities?: EntityReferenceMap;
  /** Chunk citations embedded in the message. */
  chunkCitations?: ChunkCitationMap;
}

export default function MessageContent({
  isUser,
  loading,
  isLatest,
  content,
  referencedEntities,
  chunkCitations,
}: MessageContentProps) {
  const showThinking = !isUser && loading && isLatest && (!content || content.trim().length === 0);

  return (
    <Box
      sx={{
        color: isUser ? 'common.white' : 'text.primary',
        ...markdownContentSx,
      }}
    >
      {showThinking ? (
        <Typography
          sx={{
            fontStyle: 'italic',
            color: 'text.secondary',
          }}
        >
          Thinking<LoadingDots />
        </Typography>
      ) : (
        <ChatMarkdown
          content={content}
          referencedEntities={referencedEntities}
          chunkCitations={chunkCitations}
        />
      )}
    </Box>
  );
}
