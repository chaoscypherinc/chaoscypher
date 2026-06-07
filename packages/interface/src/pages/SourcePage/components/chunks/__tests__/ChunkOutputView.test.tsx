// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect } from 'vitest';
import { ChunkOutputView } from '../ChunkOutputView';

const entities = [
  { name: 'TLR4', type: 'protein', confidence: 0.94, chunk_index: 7 },
  { name: 'MyD88', type: 'protein', confidence: 0.91, chunk_index: 7 },
  { name: 'OtherEnt', type: 'protein', confidence: 0.8, chunk_index: 99 }, // different chunk
] as any[];

const relationships = [
  { from: 'TLR4', to: 'MyD88', type: 'binds_to', confidence: 0.9 },
] as any[];

const task = {
  id: 't7',
  chunk_index: 7,
  status: 'completed',
  llm_response_json: '{"entities":[]}',
  filtering_log: null,
} as any;

describe('ChunkOutputView (controlled by per-chunk showFiltered prop)', () => {
  it('renders only entities for this chunk_index', () => {
    render(
      <ChunkOutputView
        chunkIndex={7}
        entities={entities}
        relationships={relationships}
        task={task}
        showFiltered={false}
      />,
    );
    expect(screen.getAllByText('TLR4').length).toBeGreaterThan(0);
    expect(screen.getAllByText('MyD88').length).toBeGreaterThan(0);
    expect(screen.queryByText('OtherEnt')).toBeNull();
  });

  it('renders relationship triples', () => {
    render(
      <ChunkOutputView
        chunkIndex={7}
        entities={entities}
        relationships={relationships}
        task={task}
        showFiltered={false}
      />,
    );
    expect(screen.getByText(/binds_to/)).toBeInTheDocument();
  });

  it('does not render filtered panel when showFiltered=true but log is null', () => {
    render(
      <ChunkOutputView
        chunkIndex={7}
        entities={entities}
        relationships={relationships}
        task={task}
        showFiltered={true}
      />,
    );
    expect(screen.queryByText(/FILTERED OUT/i)).toBeNull();
  });

  it('renders filtered items panel when showFiltered=true AND log present', () => {
    const taskWithLog = {
      ...task,
      filtering_log: {
        version: 1,
        total_removed: 1,
        stages: [
          {
            stage: 'structural_entity_filter',
            input_count: 2,
            removed_count: 1,
            items: [{ item_type: 'entity', name: 'Chapter 4', entity_type: '', reason: 'match' }],
          },
        ],
      },
    };
    render(
      <ChunkOutputView
        chunkIndex={7}
        entities={entities}
        relationships={relationships}
        task={taskWithLog}
        showFiltered={true}
      />,
    );
    expect(screen.getByText(/FILTERED OUT/i)).toBeInTheDocument();
    expect(screen.getByText('Chapter 4')).toBeInTheDocument();
  });

  it('hides filtered items panel when showFiltered=false even with log present', () => {
    const taskWithLog = {
      ...task,
      filtering_log: {
        version: 1,
        total_removed: 1,
        stages: [
          {
            stage: 'structural_entity_filter',
            input_count: 2,
            removed_count: 1,
            items: [{ item_type: 'entity', name: 'Chapter 4', entity_type: '', reason: 'match' }],
          },
        ],
      },
    };
    render(
      <ChunkOutputView
        chunkIndex={7}
        entities={entities}
        relationships={relationships}
        task={taskWithLog}
        showFiltered={false}
      />,
    );
    expect(screen.queryByText(/FILTERED OUT/i)).toBeNull();
  });

  it('raw JSON disclosure expands on click', async () => {
    render(
      <ChunkOutputView
        chunkIndex={7}
        entities={entities}
        relationships={relationships}
        task={task}
        showFiltered={false}
      />,
    );
    await userEvent.click(screen.getByText(/raw llm json response/i));
    expect(screen.getByText(/"entities":\[\]/)).toBeInTheDocument();
  });
});
