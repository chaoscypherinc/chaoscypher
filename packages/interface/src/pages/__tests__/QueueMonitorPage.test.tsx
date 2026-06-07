// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import QueueMonitorPage from '../QueueMonitorPage';

vi.mock('../../services/api/client', () => installApiClientMock());

describe('QueueMonitorPage', () => {
  it('renders the queue monitor with its toolbar and empty task table', async () => {
    render(
      <Routes>
        <Route path="/queues" element={<QueueMonitorPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/queues'] }) },
    );

    // The toolbar's page title proves the correct page (not a blank/loading
    // screen or a different route) finished its initial data load. With the
    // default API mock both queue queries resolve to `{}`, so the loading
    // gate clears and the toolbar renders.
    expect(
      await screen.findByRole('heading', { name: 'Queues' }),
    ).toBeInTheDocument();

    // The task table renders its "Active Tasks" section header and, because
    // the mock yields zero tasks, its empty-state row. This proves the
    // data-loaded branch executed rather than the page rendering nothing.
    expect(screen.getByRole('heading', { name: 'Active Tasks' })).toBeInTheDocument();
    expect(screen.getByText('No active tasks')).toBeInTheDocument();
  });
});
