// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * NodeDetailPage smoke tests.
 *
 * Pins the page's behaviour across the TanStack Query migration: it loads the
 * entity plus its template (dependent query) and connection count, renders
 * them, surfaces a load error, saves an edited label via PATCH, and deletes +
 * navigates back to the list. Mocks at the apiClient layer so the real service
 * modules and query hooks run unchanged — the same test passes for both the
 * legacy fetch+useState implementation and the migrated useQuery one.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import NodeDetailPage from '../NodeDetailPage';
import { apiClient } from '../../services/api/client';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const NODE = {
  id: 'n1',
  label: 'Ada Lovelace',
  template_id: 'tpl1',
  properties: { occupation: 'mathematician' },
  tags: ['historical'],
  created_at: '2026-05-20T00:00:00Z',
  updated_at: '2026-05-21T00:00:00Z',
  citation_count: 3,
  edge_count: 2,
};
const TEMPLATE = { id: 'tpl1', name: 'Person Template' };
const CONNECTIONS = {
  data: [],
  pagination: { total: 0, page: 1, page_size: 20, total_pages: 0, has_next: false, has_prev: false },
};

function mockHappyPath() {
  mockedApiClient.get.mockImplementation((url: string) => {
    if (url === '/nodes/n1') return Promise.resolve({ data: NODE });
    if (url === '/templates/tpl1') return Promise.resolve({ data: TEMPLATE });
    if (url === '/nodes/n1/connections') return Promise.resolve({ data: CONNECTIONS });
    if (url === '/nodes/n1/citations') {
      return Promise.resolve({ data: { data: [], pagination: { ...CONNECTIONS.pagination, total: 3 } } });
    }
    return Promise.resolve({ data: {} });
  });
}

function renderPage() {
  return render(
    <Routes>
      <Route path="/nodes/:nodeId" element={<NodeDetailPage />} />
      <Route path="/nodes" element={<div>Entities List</div>} />
    </Routes>,
    { wrapper: makeWrapper({ initialEntries: ['/nodes/n1'] }) },
  );
}

describe('NodeDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the detail layout for a stub node id', async () => {
    render(
      <Routes>
        <Route path="/nodes/:nodeId" element={<NodeDetailPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/nodes/test-node-1'] }) },
    );
    // The default mock resolves the node query to `{}` (truthy entity), so the
    // page leaves its loading/error branches and renders the loaded detail
    // layout. Assert on structural chrome that is independent of entity
    // content: the Details tab and the header Edit action. These fail if the
    // page rendered nothing, stuck on the spinner, or fell into the error view.
    expect(await screen.findByRole('tab', { name: 'Details' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Raw JSON' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /edit/i })).toBeTruthy();
  });

  it('loads and renders the entity with its template', async () => {
    mockHappyPath();
    renderPage();

    // Header title is the entity label.
    expect(await screen.findByText('Ada Lovelace')).toBeTruthy();
    // Template name resolves a tick later via the dependent template query.
    expect(await screen.findByText('Person Template')).toBeTruthy();
  });

  it('moves provenance out of the properties list into the collapsed metadata card', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/nodes/n1') {
        return Promise.resolve({
          data: {
            ...NODE,
            properties: {
              occupation: 'mathematician',
              source_document_name: 'war_and_peace_tiny.txt',
              source_document_id: 'doc-xyz',
              ingested_at: '2026-05-26T17:17:16Z',
            },
          },
        });
      }
      if (url === '/templates/tpl1') return Promise.resolve({ data: TEMPLATE });
      if (url === '/nodes/n1/connections') return Promise.resolve({ data: CONNECTIONS });
      // source_document_id enables the source-images query; return an array.
      if (url === '/sources/doc-xyz/images') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: {} });
    });
    renderPage();

    await screen.findByText('Ada Lovelace');

    // Document name surfaces in the collapsed metadata summary.
    expect(await screen.findByText('war_and_peace_tiny.txt')).toBeTruthy();
    // Provenance keys are pulled out of the front-and-center properties list.
    expect(screen.queryByText('source_document_id')).toBeNull();
    expect(screen.queryByText('source_document_name')).toBeNull();
    expect(screen.queryByText('ingested_at')).toBeNull();
    // The genuinely-extracted property still shows.
    expect(screen.getByText('occupation')).toBeTruthy();
  });

  it('shows an error when the entity fails to load', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/nodes/n1') return Promise.reject(new Error('boom'));
      return Promise.resolve({ data: {} });
    });
    renderPage();

    expect(await screen.findByText('Failed to load entity')).toBeTruthy();
  });

  it('saves an edited label via PATCH', async () => {
    mockHappyPath();
    mockedApiClient.patch.mockResolvedValue({ data: { ...NODE, label: 'Grace Hopper' } });
    renderPage();

    await screen.findByText('Ada Lovelace');

    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    const labelInput = screen.getByLabelText('Label') as HTMLInputElement;
    fireEvent.change(labelInput, { target: { value: 'Grace Hopper' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(mockedApiClient.patch).toHaveBeenCalledWith(
        '/nodes/n1',
        expect.objectContaining({ label: 'Grace Hopper' }),
      );
    });
  });

  it('deletes the entity and navigates back to the list', async () => {
    mockHappyPath();
    mockedApiClient.delete.mockResolvedValue({ data: {} });
    renderPage();

    await screen.findByText('Ada Lovelace');

    // Header Delete button is the only "Delete" until the dialog opens.
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(mockedApiClient.delete).toHaveBeenCalledWith('/nodes/n1', expect.anything());
    });
    expect(await screen.findByText('Entities List')).toBeTruthy();
  });
});
