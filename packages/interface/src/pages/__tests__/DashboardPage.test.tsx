// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, waitFor, fireEvent } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { apiClient } from '../../services/api/client';
import { makeWrapper } from '../../test/renderWithProviders';
import { UploadDialogContext } from '../../contexts/UploadDialogContext';
import DashboardPage from '../DashboardPage';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<typeof installApiClientMock>['apiClient'];

/** `GET /databases/current` answers with ``notice``; every other read is empty. */
function currentDatabaseWith(notice: string | null) {
  mockedApiClient.get.mockImplementation((url: string) =>
    Promise.resolve(
      url === '/databases/current' ? { data: { current: 'db', info: {}, notice } } : { data: {} },
    ),
  );
}

describe('DashboardPage', () => {
  it('renders without throwing', async () => {
    const { container } = render(
      <Routes>
        <Route path="/" element={<DashboardPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/'] }) },
    );

    // AuthProvider + data fetching are async; let the initial effects settle.
    await waitFor(() => {
      expect(container).toBeTruthy();
    });
  });

  it('opens the upload dialog when the Add Source pill is clicked', async () => {
    const openUploadDialog = vi.fn();

    const { findByRole } = render(
      <UploadDialogContext.Provider value={{ openUploadDialog }}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
        </Routes>
      </UploadDialogContext.Provider>,
      { wrapper: makeWrapper({ initialEntries: ['/'] }) },
    );

    const button = await findByRole('button', { name: /add source/i });
    fireEvent.click(button);

    expect(openUploadDialog).toHaveBeenCalledTimes(1);
  });

  it('shows the database notice as one line when the database predates the core types', async () => {
    const notice =
      'This database was made before the core entity types and is not migrated in place: ' +
      'export it with the previous version, update, then import the export.';
    currentDatabaseWith(notice);

    const { findByRole } = render(
      <Routes>
        <Route path="/" element={<DashboardPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/'] }) },
    );

    const status = await findByRole('status');
    expect(status.textContent).toContain('export it with the previous version');
    mockedApiClient.get.mockResolvedValue({ data: {} });
  });

  it('shows no notice for a database on the core types', async () => {
    currentDatabaseWith(null);

    const { findByRole, queryByRole } = render(
      <Routes>
        <Route path="/" element={<DashboardPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/'] }) },
    );

    await findByRole('button', { name: /add source/i });
    await waitFor(() => expect(mockedApiClient.get).toHaveBeenCalledWith('/databases/current'));
    expect(queryByRole('status')).toBeNull();
    mockedApiClient.get.mockResolvedValue({ data: {} });
  });

  it('mounts the background on first render, without waiting on the stats queries', async () => {
    // /counts and /quality/summary never answer (a slow summary on a large
    // database); every other read is empty.
    const pending = new Promise<never>(() => {});
    mockedApiClient.get.mockImplementation((url: string) =>
      url === '/counts' || url === '/quality/summary' ? pending : Promise.resolve({ data: {} }),
    );

    const { getByTestId, getByRole, findByRole } = render(
      <Routes>
        <Route path="/" element={<DashboardPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/'] }) },
    );

    // the background is there at once, and its graph reads start beside the stats
    expect(getByTestId('dashboard-graph-canvas')).toBeInTheDocument();
    await waitFor(() => expect(mockedApiClient.get).toHaveBeenCalledWith('/nodes', expect.anything()));
    // the stats still wait for their numbers, while the page around them is usable
    expect(getByRole('progressbar')).toBeInTheDocument();
    expect(await findByRole('button', { name: /add source/i })).toBeInTheDocument();
    mockedApiClient.get.mockResolvedValue({ data: {} });
  });
});
