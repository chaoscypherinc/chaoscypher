// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import EdgesPage from '../EdgesPage';

vi.mock('../../services/api/client', () => installApiClientMock());

describe('EdgesPage', () => {
  it('renders the Relationships page heading once loading settles', async () => {
    render(
      <Routes>
        <Route path="/edges" element={<EdgesPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/edges'] }) },
    );

    // The page shows a full-page LoadingState until the initial data fetch
    // resolves, so wait for the page's own "Relationships" heading to appear.
    // This proves the page mounted, finished loading, and rendered its real
    // content (not a blank screen, the loading spinner, or a different page).
    const heading = await screen.findByRole('heading', { name: /relationships/i });
    expect(heading).toBeInTheDocument();
  });
});
