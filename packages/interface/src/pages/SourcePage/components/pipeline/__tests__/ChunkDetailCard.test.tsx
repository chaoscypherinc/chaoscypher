// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ChunkDetailCard } from '../details/ChunkDetailCard';
import type { ExtractionTask } from '../../../../../types';

const makeTask = (overrides: Partial<ExtractionTask> = {}): ExtractionTask =>
  ({
    id: 't1',
    job_id: 'j1',
    chunk_index: 7,
    status: 'failed',
    created_at: '2026-05-16',
    retry_count: 2,
    entity_count: 0,
    relationship_count: 0,
    invalid_relationship_count: 0,
    error_message: 'output token limit exceeded',
    llm_duration_ms: 4200,
    input_tokens: 3841,
    output_tokens: 2048,
    ...overrides,
  }) as ExtractionTask;

describe('ChunkDetailCard', () => {
  it('renders nothing when task is null and not loading', () => {
    const { container } = render(
      <ChunkDetailCard task={null} loading={false} onRerun={vi.fn()} onViewChunk={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders skeleton while loading', () => {
    render(<ChunkDetailCard task={null} loading={true} onRerun={vi.fn()} onViewChunk={vi.fn()} />);
    expect(screen.getByTestId('chunk-detail-skeleton')).toBeInTheDocument();
  });

  it('renders header, attempts, error, tokens, duration and stat labels but NO text body', () => {
    render(<ChunkDetailCard task={makeTask()} loading={false} onRerun={vi.fn()} onViewChunk={vi.fn()} />);
    expect(screen.getByText(/Chunk 7/)).toBeInTheDocument();
    expect(screen.getByText(/3 attempts/)).toBeInTheDocument();
    expect(screen.getByText(/output token limit exceeded/)).toBeInTheDocument();
    expect(screen.getByText(/3,841/)).toBeInTheDocument();
    expect(screen.getByText(/4\.2s/)).toBeInTheDocument();
    expect(screen.getByText('Entities')).toBeInTheDocument();
    expect(screen.getByText('Relationships')).toBeInTheDocument();
    // Crucial: no raw text dump.
    expect(screen.queryByText(/llm_response_json/i)).toBeNull();
  });

  it('[View chunk] fires onViewChunk(task) so the caller can deep-link by member chunk id', () => {
    const onViewChunk = vi.fn();
    const task = makeTask({ small_chunk_ids: ['chunk-aaa', 'chunk-bbb'] });
    render(<ChunkDetailCard task={task} loading={false} onRerun={vi.fn()} onViewChunk={onViewChunk} />);
    fireEvent.click(screen.getByRole('button', { name: /view chunk/i }));
    expect(onViewChunk).toHaveBeenCalledWith(task);
  });

  it('[Rerun chunk] fires onRerun(task_id)', () => {
    const onRerun = vi.fn();
    render(<ChunkDetailCard task={makeTask()} loading={false} onRerun={onRerun} onViewChunk={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /rerun chunk/i }));
    expect(onRerun).toHaveBeenCalledWith('t1');
  });

  it('disables the rerun button and flips its label while isRerunning', () => {
    const onRerun = vi.fn();
    render(
      <ChunkDetailCard task={makeTask()} loading={false} onRerun={onRerun} onViewChunk={vi.fn()} isRerunning />,
    );
    const button = screen.getByRole('button', { name: /rerunning/i });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onRerun).not.toHaveBeenCalled();
  });
});
