// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { fakeSettings, makeWrapper } from '../../test/renderWithProviders';
import { apiClient } from '../../services/api/client';
import SettingsPage from '../SettingsPage';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

/**
 * The page gates on a fully-formed settings object (specifically
 * `settings.export`) before leaving its LoadingState. Route the two
 * mount-time GETs (`/settings`, `/databases`) so `useSettingsActions`
 * resolves real settings and the orchestrator renders its loaded body.
 */
function mockLoadedSettings(): void {
  mockedApiClient.get.mockImplementation((url: string) => {
    if (url === '/settings') {
      return Promise.resolve({ data: fakeSettings });
    }
    if (url === '/databases') {
      return Promise.resolve({
        data: { databases: [{ name: 'default', size: 1024 }] },
      });
    }
    return Promise.resolve({ data: {} });
  });
}

function renderPage() {
  return render(
    <Routes>
      <Route path="/settings" element={<SettingsPage />} />
    </Routes>,
    { wrapper: makeWrapper({ initialEntries: ['/settings'] }) },
  );
}

describe('SettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders without throwing', async () => {
    const { container } = renderPage();
    await waitFor(() => expect(container).toBeTruthy());
  });

  it('renders the settings orchestrator with its heading and tab navigation once settings load', async () => {
    mockLoadedSettings();
    renderPage();

    // The page heading proves the loaded SettingsPage body rendered — not a
    // blank screen, the LoadingState, or a different route. `findBy*` waits for
    // the async mount-time settings fetch to resolve.
    expect(
      await screen.findByRole('heading', { name: /^settings$/i }),
    ).toBeInTheDocument();

    // The default (General) settings tab confirms the Tabs strip rendered.
    expect(screen.getByRole('tab', { name: /general/i })).toBeInTheDocument();
    // The persistence affordance at the bottom of the loaded body.
    expect(
      screen.getByRole('button', { name: /save settings/i }),
    ).toBeInTheDocument();
  });
});
