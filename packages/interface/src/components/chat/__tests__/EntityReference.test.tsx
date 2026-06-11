// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * EntityReference hover card (Phase 3b):
 * - never shows raw IDs (template_id fallback removed);
 * - shows up to 3 top properties (priority keys first, id-like skipped,
 *   long values truncated);
 * - lazily fetches details on tooltip open when the turn's tools didn't
 *   provide any, with a per-id cache and silent degradation on failure.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { ThemeProvider, createTheme } from '@mui/material';
import EntityReference from '../EntityReference';
import type { EntityReferenceSummary } from '../../../types';

const theme = createTheme({ palette: { mode: 'dark' } });

function renderChip(
  entity: EntityReferenceSummary,
  onFetchEntity?: (id: string, type: 'node' | 'edge') => Promise<EntityReferenceSummary | null>,
) {
  render(
    <MemoryRouter>
      <ThemeProvider theme={theme}>
        <EntityReference entity={entity} onFetchEntity={onFetchEntity} />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

/** Hover the chip and wait for the tooltip to appear. */
async function openTooltip(label: string) {
  fireEvent.mouseOver(screen.getByText(label));
  await screen.findByRole('tooltip');
}

describe('EntityReference hover card content', () => {
  it('never shows a raw template_id as the type line', async () => {
    renderChip({
      id: 'node_t1',
      type: 'node',
      label: 'Pierre',
      template_id: 'tpl_9f8e7d6c-1234-5678-9abc-def012345678',
      description: 'a count',
    });
    await openTooltip('Pierre');
    expect(screen.getByText('a count')).toBeInTheDocument();
    expect(screen.queryByText(/tpl_9f8e7d6c/)).not.toBeInTheDocument();
  });

  it('shows up to 3 properties, priority keys first, skipping id-like entries', async () => {
    renderChip({
      id: 'node_t2',
      type: 'node',
      label: 'Natasha',
      description: 'a Rostov',
      properties: {
        parent_id: 'node_abc123',
        rank: 'countess',
        status: 'alive',
        residence: 'Moscow',
        age: 17,
      },
    });
    await openTooltip('Natasha');
    // Priority key first…
    expect(screen.getByText(/status/i)).toBeInTheDocument();
    expect(screen.getByText('alive')).toBeInTheDocument();
    // …then fill order; only 3 total, so 'age' (4th non-id key) is dropped.
    expect(screen.getByText('countess')).toBeInTheDocument();
    expect(screen.getByText('Moscow')).toBeInTheDocument();
    expect(screen.queryByText('17')).not.toBeInTheDocument();
    // id-like keys/values are never shown.
    expect(screen.queryByText(/node_abc123/)).not.toBeInTheDocument();
  });

  it('truncates long property values', async () => {
    renderChip({
      id: 'node_t3',
      type: 'node',
      label: 'Andrei',
      properties: { motto: 'x'.repeat(120) },
    });
    await openTooltip('Andrei');
    const value = screen.getByText(/^x+\.\.\.$/);
    expect(value.textContent!.length).toBeLessThanOrEqual(60);
  });
});

describe('EntityReference lazy fetch', () => {
  it('fetches details on tooltip open when none were provided', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      id: 'node_t4',
      type: 'node' as const,
      label: 'Kutuzov',
      description: 'field marshal of the Russian army',
    });
    renderChip({ id: 'node_t4', type: 'node', label: 'Kutuzov' }, fetcher);

    const chip = screen.getByText('Kutuzov'); // chip label (tooltip not open yet)
    fireEvent.mouseOver(chip);
    await screen.findByRole('tooltip');
    await waitFor(() =>
      expect(screen.getByText('field marshal of the Russian army')).toBeInTheDocument(),
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith('node_t4', 'node');

    // Re-opening must not refetch (cached).
    fireEvent.mouseOut(chip);
    fireEvent.mouseOver(chip);
    await screen.findByRole('tooltip');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('does not fetch when the entity already has details', async () => {
    const fetcher = vi.fn();
    renderChip(
      { id: 'node_t5', type: 'node', label: 'Helene', description: 'a Kuragin' },
      fetcher,
    );
    await openTooltip('Helene');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('degrades silently when the fetch fails', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('offline'));
    renderChip({ id: 'node_t6', type: 'node', label: 'Anatole' }, fetcher);
    await openTooltip('Anatole');
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    // The basic card still renders (click hint present, no crash).
    expect(screen.getByText('Click to view details')).toBeInTheDocument();
  });
});
