// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TemplateDetailPage smoke tests.
 *
 * Pins the page's behaviour across the TanStack Query migration: it loads and
 * renders a template, surfaces a load error, saves an edited name via PATCH,
 * deletes + navigates back to the list, and hides the edit/delete actions for
 * system templates. Mocks at the apiClient layer so the real service modules
 * and query hooks run unchanged.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { Routes, Route } from 'react-router';
import { installApiClientMock } from '../../test/mocks/apiClient';
import { makeWrapper } from '../../test/renderWithProviders';
import TemplateDetailPage from '../TemplateDetailPage';
import { apiClient } from '../../services/api/client';

vi.mock('../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const TEMPLATE = {
  id: 't1',
  name: 'Person',
  description: 'A person entity',
  template_type: 'node',
  is_system: false,
  properties: [],
  created_at: '2026-05-20T00:00:00Z',
  updated_at: '2026-05-21T00:00:00Z',
};

const SYSTEM_TEMPLATE = { ...TEMPLATE, id: 't1', name: 'System Person', is_system: true };

function mockTemplate(tpl: object) {
  mockedApiClient.get.mockImplementation((url: string) => {
    if (url === '/templates/t1') return Promise.resolve({ data: tpl });
    return Promise.resolve({ data: {} });
  });
}

function renderPage() {
  return render(
    <Routes>
      <Route path="/templates/:templateId" element={<TemplateDetailPage />} />
      <Route path="/templates" element={<div>Templates List</div>} />
    </Routes>,
    { wrapper: makeWrapper({ initialEntries: ['/templates/t1'] }) },
  );
}

describe('TemplateDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the detail page header for a stub template id', async () => {
    render(
      <Routes>
        <Route path="/templates/:templateId" element={<TemplateDetailPage />} />
      </Routes>,
      { wrapper: makeWrapper({ initialEntries: ['/templates/test-template-1'] }) },
    );
    // The shared DetailPageHeader always renders a "Back" button once the
    // template query settles. Asserting on it proves the detail-page chrome
    // mounted (not a blank screen, loading spinner, or the wrong page) without
    // depending on the volatile template name/dates the stub lacks.
    expect(await screen.findByRole('button', { name: 'Back' })).toBeTruthy();
  });

  it('loads and renders the template', async () => {
    mockTemplate(TEMPLATE);
    renderPage();

    // Header title is the template name.
    expect(await screen.findByText('Person')).toBeTruthy();
    // Edit/Delete actions present for a non-system template.
    expect(screen.getByRole('button', { name: /edit/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy();
  });

  it('shows an error when the template fails to load', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/templates/t1') return Promise.reject(new Error('boom'));
      return Promise.resolve({ data: {} });
    });
    renderPage();

    expect(await screen.findByText('Failed to load template')).toBeTruthy();
  });

  it('saves an edited name via PATCH', async () => {
    mockTemplate(TEMPLATE);
    mockedApiClient.patch.mockResolvedValue({ data: { ...TEMPLATE, name: 'Organisation' } });
    renderPage();

    await screen.findByText('Person');

    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    const nameInput = screen.getByLabelText('Template Name') as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: 'Organisation' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(mockedApiClient.patch).toHaveBeenCalledWith(
        '/templates/t1',
        expect.objectContaining({ name: 'Organisation' }),
      );
    });
  });

  it('deletes the template and navigates back to the list', async () => {
    mockTemplate(TEMPLATE);
    mockedApiClient.delete.mockResolvedValue({ data: {} });
    renderPage();

    await screen.findByText('Person');

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(mockedApiClient.delete).toHaveBeenCalledWith(
        '/templates/t1',
        expect.objectContaining({ params: {} }),
      );
    });
    expect(await screen.findByText('Templates List')).toBeTruthy();
  });

  it('hides edit and delete actions for system templates', async () => {
    mockTemplate(SYSTEM_TEMPLATE);
    renderPage();

    expect(await screen.findByText('System Person')).toBeTruthy();
    // System badge present; edit/delete actions absent.
    expect(screen.getByText('System')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /edit/i })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
  });
});
