// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import React from 'react';
import { HighlightColor } from '../theme/colors';

/**
 * Parse a sent_ref string (e.g., "S2,S5" or "S3-S5") into 0-based sentence indices.
 */
export function parseSentRef(sentRef: string): number[] {
  const indices: number[] = [];
  const parts = sentRef.split(',').map((s) => s.trim());
  for (const part of parts) {
    const rangeMatch = part.match(/^S(\d+)(?:-S?(\d+))?$/i);
    if (rangeMatch) {
      const start = parseInt(rangeMatch[1], 10);
      const end = rangeMatch[2] ? parseInt(rangeMatch[2], 10) : start;
      for (let i = start; i <= end; i++) {
        indices.push(i - 1); // Convert to 0-based
      }
    }
  }
  return indices;
}

/**
 * Render chunk content with highlighted sentences based on sent_ref and
 * sentence_offsets.
 */
export function renderChunkWithHighlights(
  content: string,
  sentRef?: string,
  chunkMetadata?: Record<string, unknown>,
): React.ReactNode {
  if (!sentRef || !chunkMetadata) return content;

  const offsets = chunkMetadata.sentence_offsets as
    | Array<{ start: number; end: number }>
    | undefined;
  if (!offsets || offsets.length === 0) return content;

  const indices = parseSentRef(sentRef);
  if (indices.length === 0) return content;

  const highlightRanges = indices
    .filter((i) => i >= 0 && i < offsets.length)
    .map((i) => offsets[i]);

  if (highlightRanges.length === 0) return content;

  highlightRanges.sort((a, b) => a.start - b.start);

  const segments: React.ReactNode[] = [];
  let pos = 0;
  highlightRanges.forEach((range, idx) => {
    if (range.start > pos) {
      segments.push(content.slice(pos, range.start));
    }
    segments.push(
      <mark
        key={idx}
        style={{ backgroundColor: HighlightColor, borderRadius: '2px', padding: '0 1px' }}
      >
        {content.slice(range.start, range.end)}
      </mark>,
    );
    pos = range.end;
  });
  if (pos < content.length) {
    segments.push(content.slice(pos));
  }

  return <>{segments}</>;
}
