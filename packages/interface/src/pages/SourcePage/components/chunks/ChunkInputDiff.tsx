// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
// ChunkInputDiff.tsx
import { diffWordsWithSpace } from 'diff';
import { Box } from '@mui/material';

export interface ChunkInputDiffProps {
  cleaned: string;
  raw: string;
}

export function ChunkInputDiff({ cleaned, raw }: ChunkInputDiffProps) {
  // Word-level diff: whole removed words/phrases strike through cleanly instead
  // of the character-level shredding diffChars produced. `WithSpace` keeps exact
  // whitespace/newlines, which matters in this monospace pre-wrap block.
  // Arg order diffWordsWithSpace(raw, cleaned): removed = in raw only (cleanup removals).
  const parts = diffWordsWithSpace(raw, cleaned);
  return (
    <Box
      sx={{
        fontFamily: 'IBM Plex Mono, ui-monospace, monospace',
        fontSize: '0.78rem',
        lineHeight: 1.7,
        whiteSpace: 'pre-wrap',
        bgcolor: 'rgba(91,154,95,0.04)',
        border: '1px solid rgba(91,154,95,0.2)',
        borderRadius: 0.5,
        p: 1.5,
      }}
    >
      {parts.map((p, idx) => {
        if (p.removed) {
          return (
            <span
              key={idx}
              data-removed="true"
              style={{
                background: 'rgba(244,67,54,0.20)',
                color: '#ef5350',
                textDecoration: 'line-through',
              }}
            >
              {p.value}
            </span>
          );
        }
        if (p.added) {
          // Should not happen — cleaned is a strict subset of raw — render anyway for safety.
          return <span key={idx}>{p.value}</span>;
        }
        return <span key={idx}>{p.value}</span>;
      })}
    </Box>
  );
}
