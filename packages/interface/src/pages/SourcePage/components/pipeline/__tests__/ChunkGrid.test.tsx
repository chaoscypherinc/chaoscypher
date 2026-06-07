// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ChunkGrid } from '../details/ChunkGrid';
import type { ExtractionChartTask } from '../../../../../types';

const make = (i: number, overrides: Partial<ExtractionChartTask> = {}): ExtractionChartTask => ({
  id: `c${i}`,
  chunk_index: i,
  status: 'completed',
  retry_count: 0,
  entity_count: 10 + i,
  relationship_count: 2,
  invalid_relationship_count: 0,
  ...overrides,
});

describe('ChunkGrid', () => {
  it('renders one tile per task in chunk_index order', () => {
    const tasks = [make(2), make(1), make(3)];
    render(<ChunkGrid tasks={tasks} selectedChunkId={null} onSelectChunk={vi.fn()} />);
    const cells = screen.getAllByTestId(/chunk-cell-/);
    expect(cells).toHaveLength(3);
    expect(cells[0]).toHaveAttribute('data-chunk-index', '1');
    expect(cells[1]).toHaveAttribute('data-chunk-index', '2');
    expect(cells[2]).toHaveAttribute('data-chunk-index', '3');
  });

  it('shows a "failed" badge and "—" metric for failed chunks', () => {
    render(
      <ChunkGrid
        tasks={[make(1, { status: 'failed', entity_count: 0 })]}
        selectedChunkId={null}
        onSelectChunk={vi.fn()}
      />,
    );
    // Badge + metric label both read "failed"; the metric value is an em dash.
    expect(screen.getAllByText('failed').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('shows a "retried" badge with the entity count for retried chunks', () => {
    render(
      <ChunkGrid
        tasks={[make(1, { retry_count: 2, entity_count: 18 })]}
        selectedChunkId={null}
        onSelectChunk={vi.fn()}
      />,
    );
    expect(screen.getByText('retried')).toBeInTheDocument();
    expect(screen.getByText('18')).toBeInTheDocument();
  });

  it('fires onSelectChunk with the tile id on click', () => {
    const onSelectChunk = vi.fn();
    render(<ChunkGrid tasks={[make(1)]} selectedChunkId={null} onSelectChunk={onSelectChunk} />);
    fireEvent.click(screen.getByTestId('chunk-cell-c1'));
    expect(onSelectChunk).toHaveBeenCalledWith('c1');
  });

  it('fires onSelectChunk(null) when the selected tile is clicked again', () => {
    const onSelectChunk = vi.fn();
    render(<ChunkGrid tasks={[make(1)]} selectedChunkId="c1" onSelectChunk={onSelectChunk} />);
    fireEvent.click(screen.getByTestId('chunk-cell-c1'));
    expect(onSelectChunk).toHaveBeenCalledWith(null);
  });

  it('renders empty-state line when tasks is empty', () => {
    render(<ChunkGrid tasks={[]} selectedChunkId={null} onSelectChunk={vi.fn()} />);
    expect(screen.getByText(/no chunk tasks yet/i)).toBeInTheDocument();
  });

  it('hides the rail + pager when everything fits in one window', () => {
    const tasks = Array.from({ length: 12 }, (_, i) => make(i + 1));
    render(<ChunkGrid tasks={tasks} selectedChunkId={null} onSelectChunk={vi.fn()} />);
    expect(screen.queryByTestId('chunk-status-rail')).toBeNull();
    expect(screen.queryByRole('button', { name: /next/i })).toBeNull();
    expect(screen.getAllByTestId(/chunk-cell-/)).toHaveLength(12);
  });

  it('windows large sources: shows the rail, a 24-tile page, and pages forward', () => {
    const tasks = Array.from({ length: 30 }, (_, i) => make(i + 1));
    render(<ChunkGrid tasks={tasks} selectedChunkId={null} onSelectChunk={vi.fn()} />);

    // Rail covers the whole source; only the first window of tiles renders.
    expect(screen.getByTestId('chunk-status-rail')).toBeInTheDocument();
    expect(screen.getAllByTestId(/chunk-cell-/)).toHaveLength(24);
    expect(screen.getByTestId('chunk-cell-c1')).toBeInTheDocument();
    expect(screen.queryByTestId('chunk-cell-c25')).toBeNull();
    expect(screen.getByRole('button', { name: /prev/i })).toBeDisabled();

    // Page 2 shows the remaining 6.
    fireEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(screen.getAllByTestId(/chunk-cell-/)).toHaveLength(6);
    expect(screen.getByTestId('chunk-cell-c25')).toBeInTheDocument();
    expect(screen.queryByTestId('chunk-cell-c1')).toBeNull();
    expect(screen.getByRole('button', { name: /next/i })).toBeDisabled();
  });
});
