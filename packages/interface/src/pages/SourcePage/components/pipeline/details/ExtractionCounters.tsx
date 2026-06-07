// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography } from '@mui/material';
import type { ExtractionTaskStats, Source } from '../../../../../types';

interface ExtractionCountersProps {
  source: Source;
  stats: ExtractionTaskStats | null;
}

interface CounterTileProps {
  label: string;
  value: string | number;
  color?: string;
}

function CounterTile({ label, value, color = '#aebcc6' }: CounterTileProps) {
  return (
    <Box
      sx={{
        bgcolor: 'transparent',
        border: '1px solid rgba(255,255,255,0.08)',
        p: 1,
        borderRadius: 0.5,
        textAlign: 'center',
      }}
    >
      <Typography sx={{ fontSize: '0.6rem', color: '#888', letterSpacing: 0.5 }}>{label}</Typography>
      <Typography sx={{ fontFamily: 'ui-monospace, monospace', fontSize: '1.1rem', color }}>{value}</Typography>
    </Box>
  );
}

function fmtN(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`;
  return String(n);
}

/**
 * Per-source extraction counters — the "how extraction ran" aggregates
 * (failures, retries, avg time, tokens, type-constraint activity). Rendered
 * in the expanded Pipeline Flow section on the Overview tab (relocated there
 * from the Chunks tab so the per-chunk grid can lead). Conditional rows show
 * only when their counters are non-zero.
 */
export function ExtractionCounters({ source, stats }: ExtractionCountersProps) {
  // reason: quality_metrics is loosely typed in the generated client; cast through unknown
  // to surface the funnel-specific counters without polluting the shared Source type.
  const qm =
    (source.quality_metrics as unknown as {
      llm_chunks_failed_permanent?: number;
      llm_chunks_truncated?: number;
      llm_chunks_aborted_by_loop?: number;
      llm_chunks_timed_out?: number;
      parser_lines_dropped?: number;
      chunks_rerun_total?: number;
      relationships_dropped_type_unmatched?: number;
      relationships_type_fuzzy_matched?: number;
      relationships_type_fell_through?: number;
    } | undefined) ?? {};
  const failedPerm = qm.llm_chunks_failed_permanent ?? 0;
  const retried = stats?.total_retries ?? 0;
  const avgMs = stats?.avg_duration_ms ?? 0;
  const tokens = (source.llm_total_input_tokens ?? 0) + (source.llm_total_output_tokens ?? 0);
  const truncated = qm.llm_chunks_truncated ?? 0;
  const abortedByLoop = qm.llm_chunks_aborted_by_loop ?? 0;
  const timedOut = qm.llm_chunks_timed_out ?? 0;
  const parserDropped = qm.parser_lines_dropped ?? 0;
  const rerun = qm.chunks_rerun_total ?? 0;
  const hasDetailedFailures =
    truncated > 0 || abortedByLoop > 0 || timedOut > 0 || parserDropped > 0 || rerun > 0;
  const typeDropped = qm.relationships_dropped_type_unmatched ?? 0;
  const fuzzyMatched = qm.relationships_type_fuzzy_matched ?? 0;
  const fellThrough = qm.relationships_type_fell_through ?? 0;
  const hasTypeConstraintActivity = typeDropped > 0 || fuzzyMatched > 0 || fellThrough > 0;

  return (
    <Box sx={{ mt: 1.75 }}>
      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 1, mb: 1.75 }}>
        <CounterTile label="FAILED PERM" value={failedPerm} color={failedPerm > 0 ? '#ef5350' : undefined} />
        <CounterTile label="RETRIED" value={retried} color={retried > 0 ? '#ffa726' : undefined} />
        <CounterTile label="AVG TIME" value={avgMs > 0 ? `${(avgMs / 1000).toFixed(1)}s` : '—'} />
        <CounterTile label="TOKENS" value={tokens > 0 ? fmtN(tokens) : '—'} />
      </Box>
      {hasDetailedFailures && (
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 1, mb: 1.75 }}>
          <CounterTile label="TRUNCATED" value={truncated} color={truncated > 0 ? '#ffa726' : undefined} />
          <CounterTile label="LOOP-ABORTED" value={abortedByLoop} color={abortedByLoop > 0 ? '#ffa726' : undefined} />
          <CounterTile label="TIMED OUT" value={timedOut} color={timedOut > 0 ? '#ef5350' : undefined} />
          <CounterTile label="PARSER DROPS" value={parserDropped} color={parserDropped > 0 ? '#ffa726' : undefined} />
          <CounterTile label="RERUN" value={rerun} />
        </Box>
      )}
      {hasTypeConstraintActivity && (
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1 }}>
          <CounterTile label="TYPE DROPPED" value={typeDropped} color={typeDropped > 0 ? '#ef5350' : undefined} />
          <CounterTile label="FUZZY RESCUED" value={fuzzyMatched} color={fuzzyMatched > 0 ? '#ffa726' : undefined} />
          <CounterTile label="FELL THROUGH" value={fellThrough} color={fellThrough > 0 ? '#ffa726' : undefined} />
        </Box>
      )}
    </Box>
  );
}
