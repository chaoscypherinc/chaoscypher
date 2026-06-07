// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import WorkflowSystemPage from '../WorkflowSystemPage';

vi.mock('../../services/api/client', () => installApiClientMock());

describe('WorkflowSystemPage', () => {
  it('renders the automations tab navigation', async () => {
    render(
      <Routes>
        <Route path="/automations" element={<WorkflowSystemPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/automations'] }) },
    );

    // The page is a tabbed shell over Workflows / Triggers / Tools. The tab
    // strip is static structure that renders regardless of API state, so it
    // proves the correct page mounted (not a blank screen or the wrong route).
    const tablist = await screen.findByRole('tablist', { name: /automations tabs/i });
    expect(tablist).toBeInTheDocument();

    expect(screen.getByRole('tab', { name: /workflows/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /triggers/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /tools/i })).toBeInTheDocument();
  });
});
