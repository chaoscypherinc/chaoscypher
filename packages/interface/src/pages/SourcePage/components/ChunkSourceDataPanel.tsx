// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useQuery } from '@tanstack/react-query';
import { Box, CircularProgress, Typography } from '@mui/material';

import { sourcesApi } from '../../../services/api/sources';

interface ChunkSourceDataPanelProps {
  sourceId: string;
  smallChunkIds: string[];
  cleanedInputText: string | null | undefined;
}

/**
 * Side-by-side view of:
 * - Raw chunk text (small_chunks.content joined, post-chunker but
 *   pre-LLM-prep)
 * - Cleaned LLM input (chunk_extraction_tasks.input_text,
 *   post-prep_for_extraction)
 *
 * Reveals everything `prepare_text_for_extraction` normalized
 * (BOM/ftfy/NFC/control/whitespace) without persisting per-stage
 * intermediates — both endpoints are the actual stored data.
 */
export function ChunkSourceDataPanel({
  sourceId,
  smallChunkIds,
  cleanedInputText,
}: ChunkSourceDataPanelProps) {
  const { data, isLoading } = useQuery({
    queryKey: ['source', sourceId, 'chunks-batch', smallChunkIds.join(',')],
    queryFn: () => sourcesApi.getChunksByIds(sourceId, smallChunkIds),
    enabled: smallChunkIds.length > 0,
    refetchOnWindowFocus: false,
  });

  const rawJoined = data?.chunks.map((c) => c.content).join('\n\n') ?? '';

  return (
    <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, gap: 2 }}>
      <Box sx={{ flex: 1 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Raw chunk text
        </Typography>
        <Box
          sx={{
            p: 1.5,
            background: 'rgba(0,0,0,0.3)',
            border: '1px solid rgba(255,255,255,0.06)',
            borderRadius: 1.5,
            maxHeight: 300,
            overflow: 'auto',
          }}
        >
          {isLoading ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <CircularProgress size={14} />
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Loading…
              </Typography>
            </Box>
          ) : (
            <Typography
              variant="body2"
              sx={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: '0.85rem' }}
            >
              {rawJoined || '(empty)'}
            </Typography>
          )}
        </Box>
      </Box>
      <Box sx={{ flex: 1 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Cleaned LLM input
        </Typography>
        <Box
          sx={{
            p: 1.5,
            background: 'rgba(0,0,0,0.3)',
            border: '1px solid rgba(255,255,255,0.06)',
            borderRadius: 1.5,
            maxHeight: 300,
            overflow: 'auto',
          }}
        >
          <Typography
            variant="body2"
            sx={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: '0.85rem' }}
          >
            {cleanedInputText ?? '(empty)'}
          </Typography>
        </Box>
      </Box>
    </Box>
  );
}
