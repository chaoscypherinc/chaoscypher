// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
// ChunkInputView.tsx
import { Box } from '@mui/material';
import ReactMarkdown from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';
import { ChunkInputDiff } from './ChunkInputDiff';

export interface ChunkInputViewProps {
  cleaned: string;
  rawContent: string | null;
  /**
   * Tab-level toggle (see ChunksTab.tsx). When true AND ``rawContent``
   * is available, render the inline diff overlay instead of the
   * markdown body. When ``rawContent`` is null (legacy source), this
   * prop is ignored and the markdown body always renders.
   */
  showRemoved: boolean;
}

export function ChunkInputView({ cleaned, rawContent, showRemoved }: ChunkInputViewProps) {
  const showingOverlay = showRemoved && rawContent !== null;

  if (showingOverlay) {
    return (
      <Box data-testid="chunk-input-diff">
        <ChunkInputDiff cleaned={cleaned} raw={rawContent!} />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        bgcolor: 'rgba(91,154,95,0.04)',
        border: '1px solid rgba(91,154,95,0.2)',
        borderRadius: 0.5,
        p: 1.5,
        '& p': { margin: 0, marginBottom: 1 },
      }}
    >
      <ReactMarkdown rehypePlugins={[rehypeSanitize]}>{cleaned}</ReactMarkdown>
    </Box>
  );
}
