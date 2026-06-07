// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PipelineFunnel } from '../PipelineFunnel';
import type { Source, ExtractionTaskStats } from '../../../../../types';

const makeSource = (overrides: Partial<Source> = {}): Source =>
  ({
    id: 's1',
    status: 'extracted',
    total_content_length: 142_000,
    chunk_count: 147,
    extraction_entities_count: 1847,
    extraction_relationships_count: 200,
    llm_total_calls: 50,
    llm_total_input_tokens: 30_000,
    llm_total_output_tokens: 8_000,
    quality_metrics: {
      cleaner_chars_removed: 14_000,
      llm_chunks_failed_permanent: 3,
      vector_indexing_status: 'indexed',
      vector_indexed_at: '2026-05-16T12:00:00Z',
    } as any,
    ...overrides,
  }) as Source;

const stats: ExtractionTaskStats = {
  total_tasks: 147,
  total_retries: 5,
  total_entities: 2341,
  total_relationships: 400,
  avg_entities_per_task: 16,
  avg_relationships_per_task: 3,
  total_invalid_relationships: 0,
  avg_invalid_per_task: 0,
  total_entities_filtered: 421,
  total_relationships_filtered: 0,
  max_retries_single_task: 2,
} as ExtractionTaskStats;

describe('PipelineFunnel', () => {
  it('renders 6 stage pills in order', () => {
    render(<PipelineFunnel source={makeSource()} llmStats={stats} visionJob={null} />);
    const labels = ['LOAD', 'CLEAN', 'CHUNK', 'EXTRACT', 'FILTER', 'COMMIT'];
    labels.forEach((l) => expect(screen.getByText(l)).toBeInTheDocument());
  });

  it('renders pills as static (non-interactive) — no button controls', () => {
    render(<PipelineFunnel source={makeSource()} llmStats={stats} visionJob={null} />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('does not render any drop chips on the pills (numbers live in the board)', () => {
    render(<PipelineFunnel source={makeSource()} llmStats={stats} visionJob={null} />);
    // The old per-pill chip text is gone — counts surface in the board instead.
    expect(screen.queryByText(/14\.0k removed/)).toBeNull();
    expect(screen.queryByText(/dropped/)).toBeNull();
    expect(screen.queryByText(/3 failed/)).toBeNull();
  });

  it('does not tint CLEAN/FILTER as warnings for normal removals', () => {
    const { container } = render(
      <PipelineFunnel source={makeSource()} llmStats={stats} visionJob={null} />,
    );
    // Normal cleanup / filtering is not a problem — both stay neutral.
    const clean = container.querySelector('[data-pill="clean"] [data-severity]');
    const filter = container.querySelector('[data-pill="filter"] [data-severity]');
    expect(clean).toHaveAttribute('data-severity', 'neutral');
    expect(filter).toHaveAttribute('data-severity', 'neutral');
  });

  it('renders the aligned stage stats board with a clean column', () => {
    render(<PipelineFunnel source={makeSource()} llmStats={stats} visionJob={null} />);
    expect(document.querySelector('[data-stage="clean"]')).not.toBeNull();
  });
});
