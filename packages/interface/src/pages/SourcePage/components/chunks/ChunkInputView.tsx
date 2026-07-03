// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
// ChunkInputView.tsx
import { Box, Typography } from '@mui/material';
import ReactMarkdown from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';
import { ChunkInputDiff } from './ChunkInputDiff';
import { renderChunkWithHighlights } from '../../../../utils/chunkHighlight';

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
  /**
   * Sentence ref(s) (e.g. ``"S1"`` or ``"S1,S2"``) to highlight when this
   * chunk is the target of a citation deep-link. When set (and not showing
   * the diff overlay), the cited sentence is wrapped in ``<mark>`` via
   * ``renderChunkWithHighlights`` using ``chunkMetadata.sentence_offsets``.
   */
  highlightSentRef?: string | null;
  /** Chunk metadata (carries ``sentence_offsets``); from the detail endpoint. */
  chunkMetadata?: Record<string, unknown> | null;
}

const SURFACE_SX = {
  bgcolor: 'rgba(91,154,95,0.04)',
  border: '1px solid rgba(91,154,95,0.2)',
  borderRadius: 0.5,
  p: 1.5,
} as const;

export function ChunkInputView({
  cleaned,
  rawContent,
  showRemoved,
  highlightSentRef,
  chunkMetadata,
}: ChunkInputViewProps) {
  const showingOverlay = showRemoved && rawContent !== null;

  if (showingOverlay) {
    return (
      <Box data-testid="chunk-input-diff">
        <ChunkInputDiff cleaned={cleaned} raw={rawContent!} />
      </Box>
    );
  }

  // Citation deep-link target: render the cited sentence highlighted (plain
  // text + <mark>, mirroring NodeDetailPage/SourcesTab) rather than markdown —
  // sentence_offsets index the raw text, not the markdown-rendered DOM.
  if (highlightSentRef) {
    return (
      <Box data-testid="chunk-input-highlighted" sx={SURFACE_SX}>
        <Typography variant="body2" component="div" sx={{ whiteSpace: 'pre-wrap', m: 0 }}>
          {renderChunkWithHighlights(cleaned, highlightSentRef, chunkMetadata ?? undefined)}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ ...SURFACE_SX, '& p': { margin: 0, marginBottom: 1 } }}>
      <ReactMarkdown rehypePlugins={[rehypeSanitize]}>{cleaned}</ReactMarkdown>
    </Box>
  );
}
