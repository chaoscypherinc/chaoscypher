// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { Source, SourceStats } from '../../../../../types';
import { PipelineStageColors } from '../../../../../theme/colors';

export interface PipelineStat {
  label: string;
  value: string;
  dotColor: string;
}

function fmtChars(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

/**
 * Pipeline "how it ran" stats for the Overview Pipeline Flow strip, derived
 * purely from `source` + `stats` (already loaded — no extra fetch). A stat is
 * emitted only when its backing value exists; no zeros/placeholders. Replaces
 * the old `useOverviewChips` (the per-chip navigation target is gone — the
 * strip lives inside the Pipeline Flow section now, it doesn't link out).
 */
export function buildPipelineStats(source: Source, stats: SourceStats | null): PipelineStat[] {
  const out: PipelineStat[] = [];
  const m = source.quality_metrics as
    | { cleaner_chars_removed?: number; vector_indexing_status?: string }
    | undefined;
  const loaded = source.total_content_length ?? stats?.total_content_length ?? 0;

  if (loaded > 0) {
    out.push({ label: 'Loaded', value: fmtChars(loaded), dotColor: PipelineStageColors.load });
    const removed = m?.cleaner_chars_removed ?? 0;
    if (removed > 0) {
      out.push({ label: 'Cleaned', value: fmtChars(Math.max(0, loaded - removed)), dotColor: PipelineStageColors.clean });
    }
  }
  if ((source.chunk_count ?? 0) > 0) {
    out.push({ label: 'Chunks', value: String(source.chunk_count), dotColor: PipelineStageColors.chunk });
  }
  if ((source.llm_total_calls ?? 0) > 0) {
    out.push({ label: 'LLM calls', value: String(source.llm_total_calls), dotColor: PipelineStageColors.extract });
  }
  if (m?.vector_indexing_status) {
    out.push({ label: 'Search', value: m.vector_indexing_status, dotColor: PipelineStageColors.commit });
  }
  return out;
}
