// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * LexiconPage smoke + behaviour tests.
 *
 * Pins the page across the TanStack Query migration: it loads popular
 * packages on mount, imports a package via the mutation, and degrades to a
 * single "service unavailable" panel on a 503 from the registry. Mocks at the
 * apiClient layer so the real lexicon service + query hooks run unchanged.
 * The device-auth flow (useLexiconAuth) is intentionally left imperative and
 * is exercised only insofar as it hits the auth-status endpoint on mount.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import LexiconPage from '../LexiconPage';
import { lexiconApi } from '../../services/api/lexicon';
import { apiClient } from '../../services/api/client';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const POPULAR_PACKAGE = {
  id: 'pkg-1',
  name: 'graph-essentials',
  description: 'Starter templates for graph building',
  owner_username: 'acme',
  owner_name: 'Acme Corp',
  owner_id: 'owner-1',
  is_public: true,
  package_type: 'TEMPLATES',
  star_count: 12,
  version_count: 3,
  download_count: 4500,
  created_at: 1716000000000,
  updated_at: 1716500000000,
};

function mockPopular(packages = [POPULAR_PACKAGE]) {
  // Popular packages + search both hit GET /lexicon/search*. Auth status is a
  // separate GET. Route by URL prefix.
  mockedApiClient.get.mockImplementation((url: string) => {
    if (url.startsWith('/lexicon/search')) {
      return Promise.resolve({
        data: { packages, total: packages.length, page: 1, limit: 10 },
      });
    }
    if (url === '/lexicon/auth/status') {
      return Promise.resolve({
        data: { authenticated: false, username: null, lexicon_url: null, token_present: false },
      });
    }
    return Promise.resolve({ data: {} });
  });
}

function renderPage() {
  return render(
    <Routes>
      <Route path="/lexicon" element={<LexiconPage />} />
    </Routes>,
    { wrapper: makeWrapper({ initialEntries: ['/lexicon'] }) },
  );
}

describe('LexiconPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it('renders without throwing', async () => {
    const { container } = renderPage();
    await waitFor(() => expect(container).toBeTruthy());
  });

  it('loads and renders popular packages on mount', async () => {
    mockPopular();
    renderPage();

    expect(await screen.findByText('Popular Packages')).toBeInTheDocument();
    expect(screen.getByText('graph-essentials')).toBeInTheDocument();
    // The wildcard popular query is sent with the downloads sort.
    expect(mockedApiClient.get).toHaveBeenCalledWith(
      expect.stringContaining('/lexicon/search?query=*'),
    );
  });

  it('imports a package via the import mutation and shows a success snackbar', async () => {
    mockPopular();
    mockedApiClient.post.mockImplementation((url: string) => {
      if (url === '/lexicon/import') {
        return Promise.resolve({ data: { message: 'Import of acme/graph-essentials queued.' } });
      }
      return Promise.resolve({ data: {} });
    });
    renderPage();

    await screen.findByText('graph-essentials');
    // Only one package → one Import button.
    screen.getByRole('button', { name: /^import$/i }).click();

    await waitFor(() =>
      expect(mockedApiClient.post).toHaveBeenCalledWith(
        '/lexicon/import',
        expect.objectContaining({ owner_username: 'acme', repo_name: 'graph-essentials' }),
      ),
    );
    expect(await screen.findByText(/import of acme\/graph-essentials queued/i)).toBeInTheDocument();
  });

  it('renders the "service unavailable" panel when the lexicon endpoint returns 503', async () => {
    // The 2026-05-22 graceful-degradation fix wraps lexicon ConnectError
    // as ExternalServiceError → HTTP 503. The page detects this on the
    // popular-packages-on-mount call and replaces the entire body with
    // a single "Lexicon service unavailable" panel — no broken search
    // box, no broken Login button.
    const apiError = Object.assign(new Error('Lexicon service unavailable'), {
      isApiError: true,
      status: 503,
      code: 'SERVER_ERROR',
    });
    vi.spyOn(lexiconApi, 'searchPackages').mockRejectedValueOnce(apiError);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('lexicon-unavailable')).toBeInTheDocument();
    });
    expect(screen.getByText(/Lexicon service unavailable/i)).toBeInTheDocument();
    // The Retry button is the only interactive affordance on the
    // unavailable panel.
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
    // Affordances that depend on the registry are NOT rendered.
    expect(screen.queryByRole('button', { name: /login to lexicon/i })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Search packages/i)).not.toBeInTheDocument();
  });
});
