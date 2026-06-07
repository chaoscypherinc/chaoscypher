// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect } from 'vitest';
import { StageStatsBoard } from '../StageStatsBoard';
import type { StageStatItem } from '../stageStats';
import type { FunnelStage } from '../PipelineFunnel';

const empty: Record<FunnelStage, StageStatItem[]> = {
  load: [], clean: [], chunk: [], extract: [], filter: [], commit: [],
};

describe('StageStatsBoard', () => {
  it('renders a value for a populated stage and a clean marker for empty ones', () => {
    render(<StageStatsBoard stats={{ ...empty, clean: [{ label: 'Removed', value: '5.0k' }] }} />);
    expect(screen.getByText('Removed')).toBeInTheDocument();
    expect(screen.getByText('5.0k')).toBeInTheDocument();
    // five empty stages → five "clean" markers
    expect(screen.getAllByText('✓ clean')).toHaveLength(5);
  });

  it('surfaces a counter description as a hover tooltip', async () => {
    render(
      <StageStatsBoard
        stats={{
          ...empty,
          clean: [{ label: 'Removed', value: '5.0k', description: 'Characters removed by the cleaner.' }],
        }}
      />,
    );
    await userEvent.hover(screen.getByText('Removed'));
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Characters removed by the cleaner.');
  });
});
