// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ChunkAttemptsList } from '../ChunkAttemptsList';
import type { ChunkAttemptSummary } from '../../../../services/api/sourceProcessing';

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

const makeSummary = (n: number, overrides?: Partial<ChunkAttemptSummary>): ChunkAttemptSummary => ({
  id: `a${n}`,
  chunk_task_id: 't1',
  attempt_number: n,
  snapshotted_at: `2026-05-15T0${n}:00:00Z`,
  started_at: null,
  completed_at: null,
  entity_count: 0,
  relationship_count: 0,
  invalid_relationship_count: 0,
  finish_reason: 'stop',
  aborted_by_loop: null,
  llm_duration_ms: 1200,
  input_tokens: 100,
  output_tokens: 0,
  input_text_length: 500,
  llm_response_length: 0,
  error_message: null,
  error_type: null,
  ...overrides,
});

describe('ChunkAttemptsList', () => {
  it('renders zero-state when no attempts', () => {
    render(wrap(<ChunkAttemptsList sourceId="src-1" chunkIndex={0} attempts={[]} />));
    expect(screen.getByText(/no prior attempts/i)).toBeInTheDocument();
  });

  it('renders one row per attempt with headline numbers', () => {
    render(
      wrap(
        <ChunkAttemptsList
          sourceId="src-1"
          chunkIndex={0}
          attempts={[makeSummary(1), makeSummary(2, { entity_count: 3, relationship_count: 1 })]}
        />,
      ),
    );
    expect(screen.getByText(/attempt 1/i)).toBeInTheDocument();
    expect(screen.getByText(/attempt 2/i)).toBeInTheDocument();
    expect(screen.getByText(/prior attempts \(2\)/i)).toBeInTheDocument();
  });
});
