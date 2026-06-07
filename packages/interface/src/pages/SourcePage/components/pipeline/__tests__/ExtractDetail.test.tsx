// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ExtractDetail } from '../details/ExtractDetail';
import { ExtractionCounters } from '../details/ExtractionCounters';

const stats = {
  total_tasks: 147,
  total_retries: 5,
  avg_duration_ms: 2100,
  total_entities: 2341,
  total_relationships: 400,
  avg_entities_per_task: 16,
  avg_relationships_per_task: 3,
  total_invalid_relationships: 0,
  avg_invalid_per_task: 0,
  total_entities_filtered: 0,
  total_relationships_filtered: 0,
  max_retries_single_task: 2,
  system_prompt: 'You extract entities...',
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;

describe('ExtractionCounters', () => {
  it('renders 4 counter tiles', () => {
    render(
      <ExtractionCounters
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        source={{ llm_total_input_tokens: 30000, llm_total_output_tokens: 8000, quality_metrics: {} } as any}
        stats={stats}
      />,
    );
    // 4 tile labels (exact case — description line uses lowercase "retried"/"failed")
    expect(screen.getByText('FAILED PERM')).toBeInTheDocument();
    expect(screen.getByText('RETRIED')).toBeInTheDocument();
    expect(screen.getByText('AVG TIME')).toBeInTheDocument();
    expect(screen.getByText('TOKENS')).toBeInTheDocument();
  });
});

describe('ExtractDetail', () => {
  it('renders ChunkGrid (prompts now live in their own PromptsSection)', () => {
    render(
      <ExtractDetail
        chartTasks={[
          {
            id: 'c1',
            chunk_index: 1,
            status: 'completed',
            retry_count: 0,
            entity_count: 10,
            relationship_count: 2,
            invalid_relationship_count: 0,
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
          } as any,
        ]}
        selectedChunkId={null}
        selectedTask={null}
        selectedTaskLoading={false}
        onSelectChunk={vi.fn()}
        onRerun={vi.fn()}
        onViewChunk={vi.fn()}
      />,
    );
    expect(screen.getByTestId('chunk-cell-c1')).toBeInTheDocument();
    // AIPromptsStrip was lifted out of ExtractDetail into PromptsSection.
    expect(screen.queryByText(/AI PROMPTS/i)).toBeNull();
  });
});
