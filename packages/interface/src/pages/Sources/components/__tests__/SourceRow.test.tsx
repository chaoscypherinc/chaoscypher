// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * SourceRow: a source's summary is a secondary line under its title (one
 * line, the full text on hover), not a column of its own; a source with no
 * summary shows no line at all.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Table, TableBody } from '@mui/material';
import { makeWrapper } from '../../../../test/renderWithProviders';
import { SourceRow } from '../SourceRow';
import type { UnifiedSource } from '../../../../types';

const SUMMARY = 'A short account of what doc-a covers, for telling it apart from the rest.';

function makeSource(overrides: Partial<UnifiedSource> = {}): UnifiedSource {
  return {
    id: 'doc-a',
    stage: 'queued',
    title: 'Doc A',
    source_type: 'pdf',
    size: 1,
    status: 'pending',
    created_at: '2026-11-01T00:00:00Z',
    ...overrides,
  };
}

function renderRow(source: UnifiedSource) {
  return render(
    <Table>
      <TableBody>
        <SourceRow
          source={source}
          isSelected={false}
          anySelected={false}
          onSelectionChange={vi.fn()}
          onRowClick={vi.fn()}
          onMenuOpen={vi.fn()}
        />
      </TableBody>
    </Table>,
    { wrapper: makeWrapper() },
  );
}

describe('<SourceRow /> summary', () => {
  it('shows the summary under the title', () => {
    renderRow(makeSource({ summary: SUMMARY }));

    const line = screen.getByText(SUMMARY);
    expect(screen.getByTestId('source-summary')).toBe(line);
    expect(line).toHaveAttribute('title', SUMMARY);
    expect(line).toHaveClass('MuiTypography-body2');
    expect(line).toHaveClass('MuiTypography-noWrap');
    // Under the title, in the same cell: no new column.
    const cell = screen.getByText('Doc A').closest('td');
    expect(cell).toContainElement(line);
    expect(screen.getAllByRole('cell')).toHaveLength(4);
  });

  it('shows no summary line when null', () => {
    renderRow(makeSource({ summary: null }));

    expect(screen.getByText('Doc A')).toBeInTheDocument();
    expect(screen.queryByTestId('source-summary')).not.toBeInTheDocument();
  });
});
