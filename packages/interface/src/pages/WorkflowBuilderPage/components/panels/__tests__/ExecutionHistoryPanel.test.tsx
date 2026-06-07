// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ExecutionHistoryPanel smoke tests.
 *
 * Pins the panel's behaviour across the TanStack Query migration: it lists a
 * workflow's executions, shows an empty state when there are none, surfaces a
 * load error with a Retry control, and lazily fetches per-execution detail when
 * a row is expanded. Mocks at the apiClient layer so the real service modules
 * and query hooks run unchanged.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { installApiClientMock } from '../../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../../test/renderWithProviders';
import { ExecutionHistoryPanel } from '../ExecutionHistoryPanel';
import { apiClient } from '../../../../../services/api/client';

vi.mock('../../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const EXECUTION = {
  id: 'exec-1',
  workflow_id: 'wf1',
  triggered_by: 'manual',
  inputs: {},
  status: 'completed',
  duration_ms: 500,
  created_at: '2026-05-21T00:00:00Z',
};

const EXECUTION_DETAIL = {
  ...EXECUTION,
  inputs: { foo: 'bar' },
  step_executions: [],
};

function renderPanel() {
  return render(<ExecutionHistoryPanel workflowId="wf1" />, {
    wrapper: makeWrapper(),
  });
}

describe('ExecutionHistoryPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('lists executions returned by the API', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/workflows/wf1/executions') {
        return Promise.resolve({ data: { data: [EXECUTION] } });
      }
      return Promise.resolve({ data: {} });
    });
    renderPanel();

    expect(await screen.findByText('completed')).toBeTruthy();
    expect(screen.getByText(/Recent Executions/)).toBeTruthy();
  });

  it('shows the empty state when there are no executions', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/workflows/wf1/executions') {
        return Promise.resolve({ data: { data: [] } });
      }
      return Promise.resolve({ data: {} });
    });
    renderPanel();

    expect(await screen.findByText('No executions yet')).toBeTruthy();
  });

  it('shows an error with a Retry control when loading fails', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/workflows/wf1/executions') {
        return Promise.reject(new Error('boom'));
      }
      return Promise.resolve({ data: {} });
    });
    renderPanel();

    expect(await screen.findByText('Failed to load execution history')).toBeTruthy();
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy();
  });

  it('lazily fetches execution detail when a row is expanded', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/workflows/wf1/executions') {
        return Promise.resolve({ data: { data: [EXECUTION] } });
      }
      if (url === '/workflows/wf1/executions/exec-1') {
        return Promise.resolve({ data: EXECUTION_DETAIL });
      }
      return Promise.resolve({ data: {} });
    });
    renderPanel();

    const row = await screen.findByText('completed');
    fireEvent.click(row);

    await waitFor(() => {
      expect(mockedApiClient.get).toHaveBeenCalledWith('/workflows/wf1/executions/exec-1');
    });
    // The detail view renders the execution id.
    expect(await screen.findByText(/ID: exec-1/)).toBeTruthy();
  });
});
