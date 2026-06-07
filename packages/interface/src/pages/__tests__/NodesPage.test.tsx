// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import NodesPage from '../NodesPage';

vi.mock('../../services/api/client', () => installApiClientMock());

describe('NodesPage', () => {
  it('renders the Entities page heading after the initial load settles', async () => {
    render(
      <Routes>
        <Route path="/nodes" element={<NodesPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/nodes'] }) },
    );

    // The "Entities" h4 heading is the stable identity of this page: it renders
    // once the loading state clears, regardless of which content branch
    // (table vs. empty state) is shown. Asserting on it proves the correct page
    // mounted and rendered — a blank screen or the wrong page would fail here.
    const heading = await screen.findByRole('heading', { name: 'Entities' });
    expect(heading).toBeInTheDocument();
  });
});
