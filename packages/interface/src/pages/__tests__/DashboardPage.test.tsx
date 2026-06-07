// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, waitFor, fireEvent } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import { UploadDialogContext } from '../../contexts/UploadDialogContext';
import DashboardPage from '../DashboardPage';

vi.mock('../../services/api/client', () => installApiClientMock());

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
});
