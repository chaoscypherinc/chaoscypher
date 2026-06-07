// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TriggersPage smoke tests.
 *
 * Covers the list render path and the optimistic-toggle mutation,
 * including the rollback-on-error behaviour the new TanStack hook
 * gives us (the previous local-state implementation silently left
 * the UI in the wrong state on PATCH failure).
 */

import { describe, it, expect, vi } from 'vitest';
import { render, waitFor, fireEvent, screen } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import TriggersPage from '../TriggersPage';
import { apiClient } from '../../services/api/client';

vi.mock('../../services/api/client', () => installApiClientMock());

interface FakeTrigger {
  id: string;
  name: string;
  event_source: string;
  workflow_id: string;
  filters: Record<string, unknown>;
  workflow_inputs: Record<string, unknown> | null;
  enabled: boolean;
  priority: number;
  created_at: string;
  updated_at: string;
}

function makeFakeTrigger(overrides: Partial<FakeTrigger> = {}): FakeTrigger {
  return {
    id: 't1',
    name: 'Test trigger',
    event_source: 'node.created',
    workflow_id: 'wf1',
    filters: {},
    workflow_inputs: null,
    enabled: false,
    priority: 100,
    created_at: '2026-05-17T00:00:00Z',
    updated_at: '2026-05-17T00:00:00Z',
    ...overrides,
  };
}

const mockedApiClient = apiClient as unknown as ReturnType<typeof installApiClientMock>['apiClient'];

describe('TriggersPage', () => {
  it('renders the trigger list from useTriggers', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/triggers') {
        return Promise.resolve({ data: { data: [makeFakeTrigger()] } });
      }
      if (url === '/workflows') {
        return Promise.resolve({ data: { data: [{ id: 'wf1', name: 'My Workflow' }] } });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <Routes>
        <Route path="/triggers" element={<TriggersPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/triggers'] }) },
    );

    await waitFor(() => {
      expect(screen.getByText('Test trigger')).toBeTruthy();
    });
  });

  it('optimistically toggles enabled and rolls back on PATCH failure', async () => {
    const trigger = makeFakeTrigger({ enabled: false });
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/triggers') {
        return Promise.resolve({ data: { data: [trigger] } });
      }
      if (url === '/workflows') {
        return Promise.resolve({ data: { data: [{ id: 'wf1', name: 'My Workflow' }] } });
      }
      return Promise.resolve({ data: {} });
    });

    mockedApiClient.patch.mockRejectedValueOnce(new Error('Server boom'));

    render(
      <Routes>
        <Route path="/triggers" element={<TriggersPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/triggers'] }) },
    );

    // Wait for the row to render
    const toggle = await screen.findByRole('switch');
    expect((toggle as HTMLInputElement).checked).toBe(false);

    // Click the toggle → optimistic flip to checked
    fireEvent.click(toggle);

    // Rollback fires after PATCH rejects + invalidateQueries re-fetches.
    // Final state: checked=false (matches server truth).
    await waitFor(() => {
      const refreshed = screen.getByRole('switch') as HTMLInputElement;
      expect(refreshed.checked).toBe(false);
    });

    // The error banner surfaces the failure.
    await waitFor(() => {
      expect(screen.getByText(/server boom/i)).toBeTruthy();
    });
  });
});
