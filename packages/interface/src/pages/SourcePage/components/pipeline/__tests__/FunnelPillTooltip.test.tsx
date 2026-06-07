// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { FunnelPillTooltip } from '../FunnelPillTooltip';

describe('FunnelPillTooltip', () => {
  it('renders title + explanation + data lines + footer', () => {
    render(
      <FunnelPillTooltip
        title="AI extraction"
        explanation="LLM read each chunk and surfaced entities + relationships."
        dataLines={['147 chunks · 5 retried', '3 failed permanent']}
        footerHint="click to see prompts, tasks, charts →"
      />,
    );
    expect(screen.getByText('AI extraction')).toBeInTheDocument();
    expect(
      screen.getByText(/LLM read each chunk and surfaced entities/),
    ).toBeInTheDocument();
    expect(screen.getByText('147 chunks · 5 retried')).toBeInTheDocument();
    expect(screen.getByText('3 failed permanent')).toBeInTheDocument();
    expect(
      screen.getByText('click to see prompts, tasks, charts →'),
    ).toBeInTheDocument();
  });

  it('omits dataLines block when array is empty', () => {
    const { container } = render(
      <FunnelPillTooltip
        title="Cleanup"
        explanation="Cleaned the text."
        dataLines={[]}
        footerHint="click to see details →"
      />,
    );
    // No monospace block when there's no data to show.
    expect(container.querySelector('[data-testid="tooltip-data-lines"]')).toBeNull();
  });
});
