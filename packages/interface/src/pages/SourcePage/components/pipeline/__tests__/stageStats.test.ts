// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { describe, it, expect } from 'vitest';
import { buildStageStats } from '../stageStats';
import type { Source, ExtractionTaskStats } from '../../../../../types';

const src = (qm: Record<string, unknown>): Source => ({ quality_metrics: qm } as unknown as Source);

describe('buildStageStats', () => {
  it('emits non-zero clean + filter counters and drops zeros', () => {
    const res = buildStageStats(
      src({ cleaner_chars_removed: 5000, cleaner_lines_removed: 18, structural_entities_filtered: 0, orphan_entities_filtered: 15 }),
      null,
    );
    expect(res.clean.map((i) => i.label)).toContain('Removed');
    expect(res.clean.map((i) => i.label)).toContain('Lines');
    expect(res.filter.map((i) => i.label)).toEqual(['Orphan']); // structural=0 dropped
  });

  it('drops vector_indexing_status when indexed, keeps it when degraded', () => {
    expect(buildStageStats(src({ vector_indexing_status: 'indexed' }), null).commit).toEqual([]);
    const degraded = buildStageStats(src({ vector_indexing_status: 'degraded' }), null).commit;
    expect(degraded.map((i) => i.label)).toContain('Search index');
  });

  it('summarizes extract from llmStats (chunks + retried)', () => {
    const stats = { total_tasks: 31, total_retries: 2 } as unknown as ExtractionTaskStats;
    const ex = buildStageStats(src({}), stats);
    expect(ex.extract.map((i) => i.label)).toEqual(['Chunks', 'Retried']);
  });
});
