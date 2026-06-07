// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * EdgeDetailPage smoke tests.
 *
 * Pins the page's behaviour across the TanStack Query migration: it loads
 * the edge plus its source/target nodes and template, renders them, surfaces
 * a load error, saves an edited label via PATCH, and deletes + navigates back
 * to the list. Mocks at the apiClient layer so the real service modules and
 * query hooks run unchanged — the same test passes for both the legacy
 * fetch+useState implementation and the migrated useQuery one.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import EdgeDetailPage from '../EdgeDetailPage';
import { apiClient } from '../../services/api/client';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const EDGE = {
  id: 'e1',
  source_node_id: 'n-src',
  target_node_id: 'n-tgt',
  template_id: 'tpl1',
  label: 'supports',
  properties: { weight: '5' },
  created_at: '2026-05-20T00:00:00Z',
  updated_at: '2026-05-21T00:00:00Z',
};
const SRC_NODE = { id: 'n-src', label: 'Source Node' };
const TGT_NODE = { id: 'n-tgt', label: 'Target Node' };
const TEMPLATE = { id: 'tpl1', name: 'Supports Template' };

function mockHappyPath() {
  mockedApiClient.get.mockImplementation((url: string) => {
    if (url === '/edges/e1') return Promise.resolve({ data: EDGE });
    if (url === '/nodes/n-src') return Promise.resolve({ data: SRC_NODE });
    if (url === '/nodes/n-tgt') return Promise.resolve({ data: TGT_NODE });
    if (url === '/templates/tpl1') return Promise.resolve({ data: TEMPLATE });
    return Promise.resolve({ data: {} });
  });
}

function renderPage() {
  return render(
    <Routes>
      <Route path="/edges/:edgeId" element={<EdgeDetailPage />} />
      <Route path="/edges" element={<div>Relationships List</div>} />
    </Routes>,
    { wrapper: makeWrapper({ initialEntries: ['/edges/e1'] }) },
  );
}

describe('EdgeDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads and renders the relationship with its nodes and template', async () => {
    mockHappyPath();
    renderPage();

    // Header title is the edge label.
    expect(await screen.findByText('supports')).toBeTruthy();
    // Source / target node labels render (chips appear in both main + sidebar).
    // `findAllByText` awaits the dependent node/template queries, which under
    // TanStack resolve a tick after the edge query rather than atomically.
    expect((await screen.findAllByText('Source Node')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('Target Node')).length).toBeGreaterThan(0);
    // Template name in the sidebar.
    expect(await screen.findByText('Supports Template')).toBeTruthy();
  });

  it('shows a confidence chip in the collapsed metadata summary', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/edges/e1') {
        return Promise.resolve({
          data: { ...EDGE, properties: { confidence: 0.87, sent_ref: 's3', chunk_index: 2 } },
        });
      }
      if (url === '/nodes/n-src') return Promise.resolve({ data: SRC_NODE });
      if (url === '/nodes/n-tgt') return Promise.resolve({ data: TGT_NODE });
      if (url === '/templates/tpl1') return Promise.resolve({ data: TEMPLATE });
      return Promise.resolve({ data: {} });
    });
    renderPage();

    await screen.findByText('supports');
    // Collapsed summary leads with the graded confidence chip.
    expect(await screen.findByText('Confidence 87%')).toBeTruthy();
    // Extraction signals are not in the editable properties list.
    expect(screen.queryByText('sent_ref')).toBeNull();
    expect(screen.queryByText('chunk_index')).toBeNull();
  });

  it('shows an error when the edge fails to load', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/edges/e1') return Promise.reject(new Error('boom'));
      return Promise.resolve({ data: {} });
    });
    renderPage();

    expect(await screen.findByText('Failed to load relationship')).toBeTruthy();
  });

  it('saves an edited label via PATCH', async () => {
    mockHappyPath();
    mockedApiClient.patch.mockResolvedValue({ data: { ...EDGE, label: 'reinforces' } });
    renderPage();

    await screen.findByText('supports');

    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    const labelInput = screen.getByLabelText('Label') as HTMLInputElement;
    fireEvent.change(labelInput, { target: { value: 'reinforces' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(mockedApiClient.patch).toHaveBeenCalledWith(
        '/edges/e1',
        expect.objectContaining({ label: 'reinforces' }),
      );
    });
  });

  it('deletes the relationship and navigates back to the list', async () => {
    mockHappyPath();
    mockedApiClient.delete.mockResolvedValue({ data: {} });
    renderPage();

    await screen.findByText('supports');

    // The header Delete button is the only "Delete" until the dialog opens.
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    // The confirm dialog's confirm button is also labelled "Delete".
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(mockedApiClient.delete).toHaveBeenCalledWith('/edges/e1', expect.anything());
    });
    expect(await screen.findByText('Relationships List')).toBeTruthy();
  });
});
