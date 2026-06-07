// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import type { Source, SourceStats } from '../../../../../types';
import { buildPipelineStats } from './pipelineStats';

const src = (o: Partial<Source> = {}) =>
  ({
    chunk_count: 12,
    total_content_length: 720000,
    llm_total_calls: 31,
    quality_metrics: { cleaner_chars_removed: 5000, vector_indexing_status: 'indexed' },
    ...o,
  }) as unknown as Source;

describe('buildPipelineStats', () => {
  it('emits Loaded/Cleaned/Chunks/LLM calls/Search with no navigation target', () => {
    const stats = buildPipelineStats(src(), null as unknown as SourceStats);
    expect(stats.map((s) => s.label)).toEqual(['Loaded', 'Cleaned', 'Chunks', 'LLM calls', 'Search']);
    expect(stats[0]).not.toHaveProperty('target');
    expect(stats.find((s) => s.label === 'Search')?.value).toBe('indexed');
  });

  it('omits cards whose backing value is absent (no zeros)', () => {
    const stats = buildPipelineStats(
      src({ total_content_length: 0, llm_total_calls: 0, quality_metrics: {} as Source['quality_metrics'] }),
      null as unknown as SourceStats,
    );
    expect(stats.map((s) => s.label)).toEqual(['Chunks']);
  });

  it('omits the Cleaned card when no characters were removed', () => {
    const stats = buildPipelineStats(
      src({ quality_metrics: { vector_indexing_status: 'indexed' } as Source['quality_metrics'] }),
      null as unknown as SourceStats,
    );
    expect(stats.map((s) => s.label)).toEqual(['Loaded', 'Chunks', 'LLM calls', 'Search']);
  });
});
