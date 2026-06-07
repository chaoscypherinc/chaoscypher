// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect } from 'vitest';
import { ExtractionStatTiles } from '../ExtractionStatTiles';
import type { Source } from '../../../../../../types';

function makeSource(overrides: Partial<Source> = {}): Source {
  return {
    id: 's1',
    extraction_entities_count: 33,
    extraction_relationships_count: 35,
    commit_templates_created: 15,
    commit_nodes_created: 30,
    commit_edges_created: 36,
    ...overrides,
  } as Source;
}

describe('ExtractionStatTiles', () => {
  it('renders the headline counts for each tile', () => {
    render(<ExtractionStatTiles source={makeSource()} />);
    expect(screen.getByText('33')).toBeInTheDocument();
    expect(screen.getByText('35')).toBeInTheDocument();
    expect(screen.getByText('15')).toBeInTheDocument();
  });

  it('shows a one-line plain-English tooltip on the Entities tile', async () => {
    render(<ExtractionStatTiles source={makeSource()} />);
    await userEvent.hover(screen.getByText('Entities'));
    expect(await screen.findByRole('tooltip')).toHaveTextContent(
      'Distinct entities extracted from this source.',
    );
  });

  it('no longer surfaces the confusing commit-side breakdown rows', async () => {
    render(<ExtractionStatTiles source={makeSource()} />);
    // Hover the Entities tile and confirm the old divergent counts are gone.
    await userEvent.hover(screen.getByText('Entities'));
    const tip = await screen.findByRole('tooltip');
    expect(tip).not.toHaveTextContent('Graph Nodes');
    expect(tip).not.toHaveTextContent('Final');
  });
});
