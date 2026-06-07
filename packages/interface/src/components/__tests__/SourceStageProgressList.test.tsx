// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SourceStageProgressList } from '../SourceStageProgressList';


function makeRecord(overrides: Partial<{
  total: number;
  processed: number;
  avg_ms: number | null;
  completed_at: string | null;
  extras: Record<string, unknown> | null;
}> = {}) {
  return {
    total: 184,
    processed: 47,
    avg_ms: 8200,
    started_at: '2026-05-10T18:00:00Z',
    last_activity: '2026-05-10T18:30:00Z',
    completed_at: null as string | null,
    extras: null as Record<string, unknown> | null,
    ...overrides,
  };
}


describe('SourceStageProgressList', () => {
  it('renders nothing when stage_progress is empty', () => {
    const { container } = render(
      <SourceStageProgressList stageProgress={{}} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders one indicator per active stage', () => {
    render(
      <SourceStageProgressList
        stageProgress={{
          vision: makeRecord({ processed: 47, total: 184 }),
          embedding: makeRecord({ processed: 0, total: 184, avg_ms: null }),
        }}
      />
    );
    expect(screen.getByText(/47\/184 pages/)).toBeInTheDocument();
    expect(screen.getByText(/0\/184 chunks/)).toBeInTheDocument();
  });

  it('hides completed stages', () => {
    render(
      <SourceStageProgressList
        stageProgress={{
          vision: makeRecord({ completed_at: '2026-05-10T19:00:00Z' }),
        }}
      />
    );
    expect(screen.queryByText(/47\/184 pages/)).not.toBeInTheDocument();
  });

  it('falls back to raw stage name and "items" noun for unknown stages', () => {
    render(
      <SourceStageProgressList
        stageProgress={{ my_custom_stage: makeRecord({ processed: 3, total: 5 }) }}
      />
    );
    expect(screen.getByText(/3\/5 items/)).toBeInTheDocument();
  });
});
