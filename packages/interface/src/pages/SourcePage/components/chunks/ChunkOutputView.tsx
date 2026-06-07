// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { Box, Typography } from '@mui/material';
import type { ExtractionTask, ExtractedEntity, InferredRelationship } from '../../../../types';
import { ChunkFilteredItems } from './ChunkFilteredItems';

export interface ChunkOutputViewProps {
  chunkIndex: number;
  entities: ExtractedEntity[];
  relationships: InferredRelationship[];
  task: ExtractionTask | null;
  /**
   * Per-chunk toggle hosted by ChunkRowBody. When true AND the chunk's
   * extraction task has a non-empty filtering_log, the FILTERED OUT
   * panel renders below relationships. When false (or no log
   * available) the panel is hidden — the switch in the chunk header
   * still renders but is disabled.
   */
  showFiltered: boolean;
}

export function ChunkOutputView({
  chunkIndex,
  entities,
  relationships,
  task,
  showFiltered,
}: ChunkOutputViewProps) {
  const filteringLog = task?.filtering_log ?? null;

  const chunkEntities = entities.filter((e) => e.chunk_index === chunkIndex);
  const entityNames = new Set(chunkEntities.map((e) => e.name));
  const chunkRels = relationships.filter((r) => {
    const from = r.from ?? '';
    const to = r.to ?? '';
    return entityNames.has(from) || entityNames.has(to);
  });

  // Token usage for this group's LLM call. Surfaced here (not just on the
  // Chunks tab's ChunkDetailCard in the Chunk Overview band) so the cost
  // of the call sits next to the entities/relationships it produced.
  // Hidden when the task carries no token counts (e.g. legacy rows that
  // predate token tracking).
  const hasTokenUsage =
    !!task && (task.input_tokens != null || task.output_tokens != null);

  return (
    <Box>
      {hasTokenUsage && (
        <Typography
          sx={{
            fontFamily: 'ui-monospace, monospace',
            fontSize: '0.65rem',
            color: '#888',
            mb: 1,
          }}
        >
          Tokens: {(task!.input_tokens ?? 0).toLocaleString()} in ·{' '}
          {(task!.output_tokens ?? 0).toLocaleString()} out
          {task!.llm_duration_ms != null && ` · ${(task!.llm_duration_ms / 1000).toFixed(1)}s`}
        </Typography>
      )}
      <Box
        sx={{
          bgcolor: 'rgba(186,103,179,0.04)',
          border: '1px solid rgba(186,103,179,0.25)',
          borderRadius: 0.5,
          p: 1.5,
          mb: 1,
        }}
      >
        <Typography sx={{ fontSize: '0.6rem', color: '#aaa', letterSpacing: 0.5, mb: 1 }}>
          ENTITIES KEPT ({chunkEntities.length})
        </Typography>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
            gap: 0.75,
            fontFamily: 'ui-monospace, monospace',
            fontSize: '0.75rem',
          }}
        >
          {chunkEntities.map((e) => (
            <Box
              key={e.name}
              sx={{
                bgcolor: 'rgba(255,255,255,0.04)',
                p: 1,
                borderRadius: 0.25,
                borderLeft: '3px solid #d99cd3',
              }}
            >
              <Box sx={{ color: '#fff' }}>{e.name}</Box>
              <Box sx={{ color: '#888', fontSize: '0.65rem' }}>
                {e.type} · conf {(e.confidence ?? 0).toFixed(2)}
              </Box>
            </Box>
          ))}
        </Box>
      </Box>

      {chunkRels.length > 0 && (
        <Box
          sx={{
            bgcolor: 'rgba(186,103,179,0.04)',
            border: '1px solid rgba(186,103,179,0.25)',
            borderRadius: 0.5,
            p: 1.5,
            mb: 1,
          }}
        >
          <Typography sx={{ fontSize: '0.6rem', color: '#aaa', letterSpacing: 0.5, mb: 1 }}>
            RELATIONSHIPS KEPT ({chunkRels.length})
          </Typography>
          <Box sx={{ fontFamily: 'ui-monospace, monospace', fontSize: '0.75rem', lineHeight: 1.7 }}>
            {chunkRels.map((r, idx) => (
              <Box key={idx}>
                <span style={{ color: '#d99cd3' }}>{r.from ?? `#${r.source}`}</span>
                <span style={{ color: '#888' }}> — {r.type} →</span>{' '}
                <span style={{ color: '#d99cd3' }}>{r.to ?? `#${r.target}`}</span>
              </Box>
            ))}
          </Box>
        </Box>
      )}

      {showFiltered && filteringLog && <ChunkFilteredItems filteringLog={filteringLog} />}

      {task?.llm_response_json && (
        <Box
          component="details"
          sx={{
            mt: 1,
            bgcolor: 'rgba(255,255,255,0.02)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 0.5,
            p: 1,
            fontSize: '0.7rem',
          }}
        >
          <Box component="summary" sx={{ cursor: 'pointer', color: '#aaa' }}>
            ▸ Raw LLM JSON response
          </Box>
          <Box
            component="pre"
            sx={{
              fontFamily: 'ui-monospace, monospace',
              fontSize: '0.65rem',
              color: '#888',
              mt: 0.75,
              overflow: 'auto',
              maxHeight: 320,
            }}
          >
            {task.llm_response_json}
          </Box>
        </Box>
      )}
    </Box>
  );
}
