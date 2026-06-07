// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * MaintenancePage smoke tests.
 *
 * Pins the page's behaviour across the TanStack Query migration: it loads the
 * pending-upgrade state, lists blocked migrations, surfaces a load error with a
 * retry, applies an upgrade via POST /upgrade/apply, rolls back via
 * POST /upgrade/rollback, and bounces to the app when the state goes ready.
 * Mocks at the apiClient layer so the real upgrade service + query hooks run
 * unchanged.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import { MaintenancePage } from '../MaintenancePage';
import { apiClient } from '../../services/api/client';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const PENDING = {
  ready: false,
  message: 'Two migrations are waiting to be applied.',
  blocked_on: [
    { revision: 'rev_abc123', tier: 'safe_auto', description: 'Add quality columns.' },
    { revision: 'rev_def456', tier: 'manual', description: 'Backfill embeddings.' },
  ],
  last_backup: '/data/backups/pre-upgrade.db',
};

// Stub a settable window.location so onSuccess redirects don't blow up jsdom.
let hrefSetter: ReturnType<typeof vi.fn<(href: string) => void>>;
const originalLocation = window.location;

beforeEach(() => {
  vi.clearAllMocks();
  hrefSetter = vi.fn();
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...originalLocation, set href(v: string) { hrefSetter(v); }, get href() { return '/'; } },
  });
});

afterEach(() => {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: originalLocation,
  });
});

function renderPage() {
  return render(<MaintenancePage />, { wrapper: makeWrapper() });
}

describe('MaintenancePage', () => {
  it('renders pending migrations once loaded', async () => {
    mockedApiClient.get.mockResolvedValue({ data: PENDING });
    renderPage();

    expect(await screen.findByText('Database upgrade required')).toBeTruthy();
    expect(screen.getByText('rev_abc123')).toBeTruthy();
    expect(screen.getByText('Add quality columns.')).toBeTruthy();
    expect(screen.getByText('Backfill embeddings.')).toBeTruthy();
    // Backup is present, so the rollback button shows.
    expect(screen.getByRole('button', { name: /roll back/i })).toBeTruthy();
  });

  it('shows an error with a retry when the pending check fails', async () => {
    mockedApiClient.get.mockRejectedValue(new Error('boom'));
    renderPage();

    expect(await screen.findByText('Upgrade check failed')).toBeTruthy();

    // Retry re-runs the pending query; make it succeed this time.
    mockedApiClient.get.mockResolvedValue({ data: PENDING });
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));

    expect(await screen.findByText('Database upgrade required')).toBeTruthy();
  });

  it('applies the upgrade and redirects to the app', async () => {
    mockedApiClient.get.mockResolvedValue({ data: PENDING });
    mockedApiClient.post.mockResolvedValue({
      data: { applied: ['rev_abc123'], current_revision: 'rev_def456', backup_path: null },
    });
    renderPage();

    await screen.findByText('Database upgrade required');
    fireEvent.click(screen.getByRole('button', { name: /apply upgrade/i }));

    await waitFor(() => {
      expect(mockedApiClient.post).toHaveBeenCalledWith('/upgrade/apply');
    });
    await waitFor(() => expect(hrefSetter).toHaveBeenCalledWith('/'));
  });

  it('rolls back and redirects to the app', async () => {
    mockedApiClient.get.mockResolvedValue({ data: PENDING });
    mockedApiClient.post.mockResolvedValue({
      data: { restored_from: '/data/backups/pre-upgrade.db', revision: null },
    });
    renderPage();

    await screen.findByText('Database upgrade required');
    fireEvent.click(screen.getByRole('button', { name: /roll back/i }));

    await waitFor(() => {
      expect(mockedApiClient.post).toHaveBeenCalledWith('/upgrade/rollback');
    });
    await waitFor(() => expect(hrefSetter).toHaveBeenCalledWith('/'));
  });

  it('bounces to the app when the upgrade is already ready', async () => {
    mockedApiClient.get.mockResolvedValue({
      data: { ready: true, message: '', blocked_on: [], last_backup: null },
    });
    renderPage();

    await waitFor(() => expect(hrefSetter).toHaveBeenCalledWith('/'));
  });
});
