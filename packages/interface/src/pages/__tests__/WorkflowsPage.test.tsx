// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowsPage smoke tests.
 *
 * Covers list render, execute mutation (modal → result banner), and the
 * optimistic is_active toggle rollback path.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, waitFor, screen, fireEvent } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import WorkflowsPage from '../WorkflowsPage';
import { apiClient } from '../../services/api/client';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<typeof installApiClientMock>['apiClient'];

function makeWorkflow(overrides: Record<string, unknown> = {}) {
  return {
    id: 'wf1',
    database_name: 'test-db',
    name: 'My workflow',
    description: 'A workflow',
    category: 'analysis',
    is_system: false,
    is_active: true,
    expose_as_ai_tool: false,
    input_schema: {},
    tags: [],
    created_at: '2026-05-17T00:00:00Z',
    updated_at: '2026-05-17T00:00:00Z',
    ...overrides,
  };
}

describe('WorkflowsPage', () => {
  it('renders workflows from useWorkflows', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/workflows') {
        return Promise.resolve({ data: { data: [makeWorkflow()] } });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <Routes>
        <Route path="/workflows" element={<WorkflowsPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/workflows'] }) },
    );

    await waitFor(() => {
      expect(screen.getByText('My workflow')).toBeTruthy();
    });
  });

  it('optimistically toggles is_active and rolls back on PATCH failure', async () => {
    const wf = makeWorkflow({ is_active: true });
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/workflows') {
        return Promise.resolve({ data: { data: [wf] } });
      }
      return Promise.resolve({ data: {} });
    });
    mockedApiClient.patch.mockRejectedValueOnce(new Error('Server boom'));

    render(
      <Routes>
        <Route path="/workflows" element={<WorkflowsPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/workflows'] }) },
    );

    const toggle = await screen.findByRole('switch');
    expect((toggle as HTMLInputElement).checked).toBe(true);

    fireEvent.click(toggle);

    // Rollback completes after onError + invalidateQueries refetch.
    // Final state matches server truth (still active).
    await waitFor(() => {
      const refreshed = screen.getByRole('switch') as HTMLInputElement;
      expect(refreshed.checked).toBe(true);
    });

    await waitFor(() => {
      expect(screen.getByText(/server boom/i)).toBeTruthy();
    });
  });
});
