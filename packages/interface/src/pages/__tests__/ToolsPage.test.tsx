// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * ToolsPage smoke tests.
 *
 * Covers list render and the delete-mutation invalidation path.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, waitFor, screen, fireEvent } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import ToolsPage from '../ToolsPage';
import { apiClient } from '../../services/api/client';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<typeof installApiClientMock>['apiClient'];

function makeUserTool(overrides: Record<string, unknown> = {}) {
  return {
    id: 'ut1',
    database_name: 'test-db',
    name: 'My user tool',
    description: 'A custom tool',
    system_tool_id: 'st1',
    configuration: {},
    tags: ['foo'],
    is_active: true,
    created_at: '2026-05-17T00:00:00Z',
    updated_at: '2026-05-17T00:00:00Z',
    ...overrides,
  };
}

describe('ToolsPage', () => {
  it('renders system and user tool lists', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/tools/system') {
        return Promise.resolve({
          data: [
            { id: 'st1', category: 'web', icon: null, name: 'Search', description: 'Search the web', input_schema: {}, output_schema: {}, version: '1.0', is_active: true },
          ],
        });
      }
      if (url === '/tools') {
        return Promise.resolve({ data: { data: [makeUserTool()] } });
      }
      return Promise.resolve({ data: {} });
    });

    render(
      <Routes>
        <Route path="/tools" element={<ToolsPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/tools'] }) },
    );

    // Tool description (the system tool's body) is the most specific match.
    await waitFor(() => {
      expect(screen.getByText('Search the web')).toBeTruthy();
    });

    // Switch to user tools tab
    const myToolsTab = screen.getByRole('tab', { name: /my tools/i });
    fireEvent.click(myToolsTab);

    await waitFor(() => {
      expect(screen.getByText('My user tool')).toBeTruthy();
    });
  });

  it('invokes DELETE through the mutation when delete is confirmed', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/tools/system') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/tools') {
        return Promise.resolve({ data: { data: [makeUserTool()] } });
      }
      return Promise.resolve({ data: {} });
    });
    mockedApiClient.delete.mockResolvedValue({ data: {} });

    render(
      <Routes>
        <Route path="/tools" element={<ToolsPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/tools'] }) },
    );

    // Switch to user tools tab
    const myToolsTab = await screen.findByRole('tab', { name: /my tools/i });
    fireEvent.click(myToolsTab);

    // Wait for user tool to render
    await screen.findByText('My user tool');

    // Click delete (aria-label varies; the icon button is the third button on the card).
    // We trigger the underlying handler by querying for the trash icon's button.
    const cardButtons = screen.getAllByRole('button');
    const deleteButton = cardButtons.find((b) => b.querySelector('[data-testid="DeleteIcon"]'));
    expect(deleteButton).toBeTruthy();
    fireEvent.click(deleteButton!);

    // Confirm in the dialog
    const confirmButton = await screen.findByRole('button', { name: /confirm|delete/i });
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(mockedApiClient.delete).toHaveBeenCalledWith('/tools/ut1');
    });
  });
});
