// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Workstream 10 — render tests for the vector-search status badge.
 *
 * Verifies each of the four states maps to the correct visible label
 * and that 'pending' is hidden (the default state should not clutter
 * every fresh source row).
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SearchStatusBadge } from '../SearchStatusBadge';

describe('<SearchStatusBadge />', () => {
  it("hides itself for 'pending' so the table doesn't show it on every fresh source", () => {
    const { container } = render(<SearchStatusBadge status="pending" />);
    // The Chip is in the DOM but display:none via the sx prop. We
    // assert the computed style rather than the inline style attribute
    // because MUI's sx serializes to a CSS class, not an inline rule.
    const chip = container.querySelector('.MuiChip-root') as HTMLElement | null;
    expect(chip).not.toBeNull();
    expect(window.getComputedStyle(chip!).display).toBe('none');
  });

  it("renders 'Search ready' for 'indexed'", () => {
    render(<SearchStatusBadge status="indexed" indexedAt="2026-05-07T12:34:56Z" />);
    expect(screen.getByText('Search ready')).toBeInTheDocument();
  });

  it("renders 'Search retrying' for 'degraded'", () => {
    render(<SearchStatusBadge status="degraded" />);
    expect(screen.getByText('Search retrying')).toBeInTheDocument();
  });

  it("renders 'Search failed' for 'failed'", () => {
    render(<SearchStatusBadge status="failed" />);
    expect(screen.getByText('Search failed')).toBeInTheDocument();
  });

  it('falls back to pending styling for an unrecognised status string', () => {
    // Defensive: if the backend introduces a new state in the future
    // the badge should not crash; it should render the (hidden) pending
    // chip so the row layout stays intact.
    const { container } = render(<SearchStatusBadge status="quantum-flux" />);
    const chip = container.querySelector('.MuiChip-root') as HTMLElement | null;
    expect(chip).not.toBeNull();
    expect(window.getComputedStyle(chip!).display).toBe('none');
  });
});
