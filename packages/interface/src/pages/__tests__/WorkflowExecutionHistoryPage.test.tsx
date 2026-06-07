// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * WorkflowExecutionHistoryPage smoke tests.
 *
 * Pins the page's behaviour across the TanStack Query migration: it loads the
 * workflow, its executions list, and stats in parallel; renders the header,
 * stats cards, and execution rows; surfaces a load error with a Retry control;
 * and fetches + shows execution detail on demand when a row is opened. Mocks at
 * the apiClient layer so the real service modules and query hooks run unchanged.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import WorkflowExecutionHistoryPage from '../WorkflowExecutionHistoryPage';
import { apiClient } from '../../services/api/client';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const WORKFLOW = {
  id: 'wf1',
  database_name: 'test-db',
  name: 'Nightly Sync',
  description: 'Syncs the graph each night',
  is_system: false,
  is_active: true,
  expose_as_ai_tool: false,
  input_schema: {},
  created_at: '2026-05-20T00:00:00Z',
  updated_at: '2026-05-20T00:00:00Z',
};

const EXECUTION = {
  id: 'exec-abcdef12',
  workflow_id: 'wf1',
  triggered_by: 'manual',
  inputs: {},
  status: 'completed',
  duration_ms: 1234,
  created_at: '2026-05-21T00:00:00Z',
};

const STATS = {
  workflow_id: 'wf1',
  total_executions: 7,
  successful_executions: 5,
  failed_executions: 2,
  cancelled_executions: 0,
  avg_duration_ms: 1500,
};

const EXECUTION_DETAIL = {
  ...EXECUTION,
  step_executions: [],
};

function mockHappyPath() {
  mockedApiClient.get.mockImplementation((url: string) => {
    if (url === '/workflows/wf1') return Promise.resolve({ data: WORKFLOW });
    if (url === '/workflows/wf1/executions/exec-abcdef12') {
      return Promise.resolve({ data: EXECUTION_DETAIL });
    }
    if (url === '/workflows/wf1/executions') {
      return Promise.resolve({ data: { data: [EXECUTION] } });
    }
    if (url === '/workflows/wf1/stats') return Promise.resolve({ data: STATS });
    return Promise.resolve({ data: {} });
  });
}

function renderPage() {
  return render(
    <Routes>
      <Route path="/automations/:workflowId/history" element={<WorkflowExecutionHistoryPage />} />
      <Route path="/automations" element={<div>Automations List</div>} />
    </Routes>,
    { wrapper: makeWrapper({ initialEntries: ['/automations/wf1/history'] }) },
  );
}

describe('WorkflowExecutionHistoryPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads the workflow, stats, and executions and renders them', async () => {
    mockHappyPath();
    renderPage();

    // Header shows the workflow name (appears in breadcrumb + heading).
    expect((await screen.findAllByText(/Nightly Sync/)).length).toBeGreaterThan(0);
    // Stats card total.
    expect(await screen.findByText('7')).toBeTruthy();
    // Execution row status chip.
    expect(await screen.findByText('completed')).toBeTruthy();
    // Truncated execution id appears in the row.
    expect(await screen.findByText(/exec-abc/)).toBeTruthy();
  });

  it('shows an error with a Retry control when loading fails', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/workflows/wf1') return Promise.reject(new Error('boom'));
      return Promise.resolve({ data: {} });
    });
    renderPage();

    expect(await screen.findByText('Failed to load execution history')).toBeTruthy();
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy();
  });

  it('fetches execution detail when a row is opened', async () => {
    mockHappyPath();
    renderPage();

    const viewButton = await screen.findByRole('button', { name: 'View Details' });
    fireEvent.click(viewButton);

    await waitFor(() => {
      expect(mockedApiClient.get).toHaveBeenCalledWith(
        '/workflows/wf1/executions/exec-abcdef12',
      );
    });
    // The detail dialog opens and renders.
    expect(await screen.findByRole('dialog')).toBeTruthy();
  });
});
