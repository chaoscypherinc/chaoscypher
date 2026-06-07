// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ChunkStatusRail } from '../details/ChunkStatusRail';
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

describe('ChunkStatusRail', () => {
  const tasks = [make(1), make(2), make(3), make(4), make(5)];

  it('renders one clickable segment per task', () => {
    render(
      <ChunkStatusRail
        tasks={tasks}
        windowStart={0}
        windowEnd={2}
        selectedChunkId={null}
        onJump={vi.fn()}
      />,
    );
    expect(screen.getByTestId('chunk-status-rail')).toBeInTheDocument();
    expect(screen.getAllByRole('button')).toHaveLength(5);
  });

  it('fires onJump with the task and its index when a segment is clicked', () => {
    const onJump = vi.fn();
    render(
      <ChunkStatusRail
        tasks={tasks}
        windowStart={0}
        windowEnd={2}
        selectedChunkId={null}
        onJump={onJump}
      />,
    );
    // Segment for chunk 4 sits at index 3 — an off-window segment.
    fireEvent.click(screen.getByRole('button', { name: /Chunk 4/ }));
    expect(onJump).toHaveBeenCalledWith(tasks[3], 3);
  });
});
