// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for DistributionChart's pager. The recharts body doesn't paint in
 * jsdom (no layout), so we assert the pagination footer: the page indicator,
 * prev/next enablement at the ends, and the single-page type count.
 */

import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { DistributionChart } from '../DistributionChart';

function dist(n: number): Record<string, number> {
  const d: Record<string, number> = {};
  for (let i = 0; i < n; i++) d[`Type ${i + 1}`] = n - i;
  return d;
}

describe('DistributionChart pagination', () => {
  it('shows a type count and no pager when items fit on one page', () => {
    render(
      <DistributionChart
        title="Entity Distribution"
        distribution={dist(5)}
        typeToTemplate={new Map()}
        defaultIcon="Hub"
      />,
    );
    expect(screen.getByText('5 types')).toBeInTheDocument();
    expect(screen.queryByLabelText('Next page')).toBeNull();
    expect(screen.queryByLabelText('Previous page')).toBeNull();
  });

  it('pages through items with prev/next, disabling at the ends', () => {
    render(
      <DistributionChart
        title="Relationship Distribution"
        distribution={dist(13)}
        typeToTemplate={new Map()}
        defaultIcon="Hub"
      />,
    );
    // 13 items / 7 per page → 2 pages.
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument();
    expect(screen.getByLabelText('Previous page')).toBeDisabled();
    const next = screen.getByLabelText('Next page');
    expect(next).not.toBeDisabled();

    fireEvent.click(next);
    expect(screen.getByText('Page 2 of 2')).toBeInTheDocument();
    expect(screen.getByLabelText('Next page')).toBeDisabled();
    expect(screen.getByLabelText('Previous page')).not.toBeDisabled();

    fireEvent.click(screen.getByLabelText('Previous page'));
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument();
  });

  it('uses the singular "type" label for a single item', () => {
    render(
      <DistributionChart
        title="Entity Distribution"
        distribution={dist(1)}
        typeToTemplate={new Map()}
        defaultIcon="Hub"
      />,
    );
    expect(screen.getByText('1 type')).toBeInTheDocument();
  });
});
